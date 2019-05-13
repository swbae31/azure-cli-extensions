# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from datetime import datetime

from knack.log import get_logger

from azure.cli.command_modules.vm.custom import get_vm, _is_linux_os
from azure.cli.command_modules.storage.storage_url_helpers import StorageResourceIdentifier
from msrestazure.tools import parse_resource_id

from .repair_utils import (
    _uses_managed_disk,
    _call_az_command,
    _clean_up_resources,
    _fetch_compatible_sku,
    _list_resource_ids_in_rg,
    _get_repair_resource_tag
)
from .exceptions import AzCommandError, SkuNotAvailableError, UnmanagedDiskCopyError

# pylint: disable=line-too-long, too-many-locals, too-many-statements

logger = get_logger(__name__)

def create(cmd, vm_name, resource_group_name, repair_password=None, repair_username=None):
    # begin progress reporting for long running operation
    cmd.cli_ctx.get_progress_controller().begin()
    cmd.cli_ctx.get_progress_controller().add(message='Running')

    target_vm = get_vm(cmd, resource_group_name, vm_name)
    is_linux = _is_linux_os(target_vm)
    target_disk_name = target_vm.storage_profile.os_disk.name
    is_managed = _uses_managed_disk(target_vm)

    if is_linux:
        os_image_name = "UbuntuLTS"
    else:
        # temp fix to mitigate Windows disk signature collision error
        if target_vm.storage_profile.image_reference \
           and target_vm.storage_profile.image_reference.version == '2016.127.20190214':
            os_image_name = "MicrosoftWindowsServer:WindowsServer:2016-Datacenter:2016.127.20190115"
        else:
            os_image_name = "MicrosoftWindowsServer:WindowsServer:2016-Datacenter:2016.127.20190214"

    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    copied_os_disk_name = vm_name + '-DiskCopy-' + timestamp
    copied_disk_uri = None
    repair_vm_name = ('repair-' + vm_name)[:15]
    repair_rg_name = 'repair-' + vm_name + '-' + timestamp
    copy_disk_id = None
    resource_tag = _get_repair_resource_tag(resource_group_name, vm_name)

    # Set up base create vm command
    create_repair_vm_command = 'az vm create -g {g} -n {n} --tag {tag} --image {image} --admin-password {password}' \
                               .format(g=repair_rg_name, n=repair_vm_name, tag=resource_tag, image=os_image_name, password=repair_password)
    # Add username field only for Windows
    if not is_linux:
        create_repair_vm_command += ' --admin-username {username}'.format(username=repair_username)

    # Overall success flag
    command_succeeded = False
    # List of created resouces
    created_resources = []

    # Main command calling block
    try:
        # fetch VM size of repair VM
        sku = _fetch_compatible_sku(target_vm)
        if not sku:
            raise SkuNotAvailableError('Failed to find compatible VM size for faulty VM OS disk within given region and subscription.')
        create_repair_vm_command += ' --size {sku}'.format(sku=sku)

        # Create New Resource Group
        create_resource_group_command = 'az group create -l {loc} -n {group_name}' \
                                        .format(loc=target_vm.location, group_name=repair_rg_name)
        logger.info('Creating resource group for repair VM and its resources...')
        _call_az_command(create_resource_group_command)

        # MANAGED DISK
        if is_managed:
            logger.info('OS disk is managed. Executing managed disk swap.\n')
            # Copy OS disk command
            copy_disk_command = 'az disk create -g {g} -n {n} --source {s} --query id -o tsv' \
                                .format(g=resource_group_name, n=copied_os_disk_name, s=target_disk_name)
            # Validate create vm create command to validate parameters before runnning copy disk command
            validate_create_vm_command = create_repair_vm_command + ' --validate'

            logger.info('Validating VM template before continuing...')
            _call_az_command(validate_create_vm_command, secure_params=[repair_password])
            logger.info('Copying OS disk of faulty VM...')
            copy_disk_id = _call_az_command(copy_disk_command).strip('\n')

            attach_disk_command = 'az vm disk attach -g {g} --vm-name {repair} --name {id}' \
                                  .format(g=repair_rg_name, repair=repair_vm_name, id=copy_disk_id)

            logger.info('Creating repair vm...')
            _call_az_command(create_repair_vm_command, secure_params=[repair_password])
            logger.info('Attaching copied disk to repair vm...')
            _call_az_command(attach_disk_command)
        # UNMANAGED DISK
        else:
            logger.info('OS disk is unmanaged. Executing unmanaged disk swap...\n')
            os_disk_uri = target_vm.storage_profile.os_disk.vhd.uri
            copied_os_disk_name = copied_os_disk_name + '.vhd'
            # TODO, validate with Tosin about using this
            storage_account = StorageResourceIdentifier(cmd.cli_ctx.cloud, os_disk_uri)
            # Validate create vm create command to validate parameters before runnning copy disk commands
            validate_create_vm_command = create_repair_vm_command + ' --validate'
            logger.info('Validating VM template before continuing...')
            _call_az_command(validate_create_vm_command, secure_params=[repair_password])

            # get storage account connection string
            get_connection_string_command = 'az storage account show-connection-string -g {g} -n {n} --query connectionString -o tsv' \
                                            .format(g=resource_group_name, n=storage_account.account_name)
            logger.info('Fetching storage account connection string...')
            connection_string = _call_az_command(get_connection_string_command).strip('\n')

            # Create Snapshot of Unmanaged Disk
            make_snapshot_command = 'az storage blob snapshot -c {c} -n {n} --connection-string "{con_string}" --query snapshot -o tsv' \
                                    .format(c=storage_account.container, n=storage_account.blob, con_string=connection_string)
            logger.info('Creating snapshot of OS disk...')
            snapshot_timestamp = _call_az_command(make_snapshot_command, secure_params=[connection_string]).strip('\n')
            snapshot_uri = os_disk_uri + '?snapshot={timestamp}'.format(timestamp=snapshot_timestamp)

            # Copy Snapshot into unmanaged Disk
            copy_snapshot_command = 'az storage blob copy start -c {c} -b {name} --source-uri {source} --connection-string "{con_string}"' \
                                    .format(c=storage_account.container, name=copied_os_disk_name, source=snapshot_uri, con_string=connection_string)
            logger.info('Creating a copy disk from the snapshot...')
            _call_az_command(copy_snapshot_command, secure_params=[connection_string])
             # Generate the copied disk uri
            copied_disk_uri = os_disk_uri.rstrip(storage_account.blob) + copied_os_disk_name
            copy_disk_id = copied_disk_uri

            # Create new repair VM with copied ummanaged disk command
            create_repair_vm_command = create_repair_vm_command + ' --use-unmanaged-disk'
            logger.info('Creating repair vm while disk copy is in progress...')
            _call_az_command(create_repair_vm_command, secure_params=[repair_password])

            logger.info('Checking if disk copy is done...')
            copy_check_command = 'az storage blob show -c {c} -n {name} --connection-string "{con_string}" --query properties.copy.status -o tsv' \
                                 .format(c=storage_account.container, name=copied_os_disk_name, con_string=connection_string)
            copy_result = _call_az_command(copy_check_command, secure_params=[connection_string]).strip('\n')
            if copy_result != 'success':
                raise UnmanagedDiskCopyError('Unmanaged disk copy failed!')

            # Attach copied unmanaged disk to new vm
            logger.info('Attaching copied disk to repair VM as data disk...')
            attach_disk_command = "az vm unmanaged-disk attach -g {g} -n {disk_name} --vm-name {vm_name} --vhd-uri {uri}" \
                                  .format(g=repair_rg_name, disk_name=copied_os_disk_name, vm_name=repair_vm_name, uri=copied_disk_uri)
            _call_az_command(attach_disk_command)

        command_succeeded = True
        created_resources = _list_resource_ids_in_rg(repair_rg_name)

    # Some error happened. Stop command and clean-up resources.
    except KeyboardInterrupt:
        logger.error("Command interrupted by user input. Cleaning up resources.")
    except AzCommandError as azCommandError:
        logger.error(azCommandError)
        logger.error("Repair swap-disk failed. Cleaning up created resources.")
    except SkuNotAvailableError as skuNotAvailableError:
        logger.error(skuNotAvailableError)
        logger.error("Please check if the current subscription can create more VM resources. Cleaning up created resources.")
    except UnmanagedDiskCopyError as unmanagedDiskCopyError:
        logger.error(unmanagedDiskCopyError)
        logger.error("Repair swap-disk failed. Please try again at another time. Cleaning up created resources.")
    finally:
        # end long running op for process
        cmd.cli_ctx.get_progress_controller().end()

    if not command_succeeded:
        _clean_up_resources(repair_rg_name, confirm=False)
        return None

    # Construct return dict
    created_resources.append(copy_disk_id)
    return_dict = {}
    return_dict['message'] = 'repair VM \'{n}\' succesfully created in resource group \'{repair_rg}\' with disk \'{d}\' attached as a data disk. ' \
                             'Copied disk created within the orignal resource group \'{rg}\'.' \
                             .format(n=repair_vm_name, repair_rg=repair_rg_name, d=copied_os_disk_name, rg=resource_group_name)
    return_dict['repairVmName'] = repair_vm_name
    return_dict['copiedDiskName'] = copied_os_disk_name
    return_dict['copiedDiskUri'] = copied_disk_uri
    return_dict['repairResouceGroup'] = repair_rg_name
    return_dict['resourceTag'] = resource_tag
    return_dict['createdResources'] = created_resources

    return return_dict

def restore(cmd, vm_name, resource_group_name, disk_name=None, repair_vm_id=None, yes=False):

    # begin progress reporting for long running operation
    cmd.cli_ctx.get_progress_controller().begin()
    cmd.cli_ctx.get_progress_controller().add(message='Running')

    target_vm = get_vm(cmd, resource_group_name, vm_name)
    is_managed = _uses_managed_disk(target_vm)

    repair_vm_id = parse_resource_id(repair_vm_id)
    repair_vm_name = repair_vm_id['name']
    repair_resource_group = repair_vm_id['resource_group']

    # Overall success flag
    command_succeeded = False

    try:
        if is_managed:
            # Detach repaired data disk command
            detach_disk_command = 'az vm disk detach -g {g} --vm-name {repair} --name {disk}' \
                                  .format(g=repair_resource_group, repair=repair_vm_name, disk=disk_name)
            # Update OS disk with repaired data disk
            attach_fixed_command = 'az vm update -g {g} -n {n} --os-disk {disk}' \
                                   .format(g=resource_group_name, n=vm_name, disk=disk_name)

            # Maybe run attach and delete concurrently
            logger.info('Detaching repaired data disk from repair VM...')
            _call_az_command(detach_disk_command)
            logger.info('Attaching repaired data disk to faulty VM as an OS disk...')
            _call_az_command(attach_fixed_command)
        else:
            # Get disk uri from disk name
            repair_vm = get_vm(cmd, repair_vm_id['resource_group'], repair_vm_id['name'])
            data_disks = repair_vm.storage_profile.data_disks
            # The params went through validator so no need for existence checks
            disk_uri = [disk.vhd.uri for disk in data_disks if disk.name == disk_name][0]

            detach_unamanged_command = 'az vm unmanaged-disk detach -g {g} --vm-name {repair} --name {disk}' \
                                  .format(g=repair_resource_group, repair=repair_vm_name, disk=disk_name)
            # Update OS disk with disk
            # storageProfile.osDisk.name="{disk}"
            attach_unmanaged_command = 'az vm update -g {g} -n {n} --set storageProfile.osDisk.vhd.uri="{uri}"' \
                                   .format(g=resource_group_name, n=vm_name, uri=disk_uri)
            logger.info('Detaching repaired data disk from repair VM...')
            _call_az_command(detach_unamanged_command)
            logger.info('Attaching repaired data disk to faulty VM as an OS disk...')
            _call_az_command(attach_unmanaged_command)
        # Clean
        _clean_up_resources(repair_resource_group, confirm=not yes)
        command_succeeded = True
    except AzCommandError as azCommandError:
        logger.error(azCommandError)
        logger.error("Repair swap-disk failed.")
    finally:
        # end long running op for process
        cmd.cli_ctx.get_progress_controller().end()

    if not command_succeeded:
        return None

    # Construct return dict
    return_dict = {}
    return_dict['message'] = '{disk} successfully attached to {n} as an OS disk.' \
                             .format(disk=disk_name, n=vm_name)

    return return_dict