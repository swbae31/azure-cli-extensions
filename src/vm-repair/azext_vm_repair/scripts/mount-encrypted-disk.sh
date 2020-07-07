#!/bin/bash
duplication_validation ()
{
#/boot/efi duplication validation
efi_cnt=`lsblk | grep -i "/boot/efi" | wc -l`
if [ "${efi_cnt}" -eq 2 ]
then
        umount /boot/efi
fi
}

get_data_disk ()
{
export data_disk=`ls -la /dev/disk/azure/scsi1/lun0 | awk -F. '{print "/dev"$7}'`
echo "The data disk is ${data_disk}"
}

create_mountpoints ()
{
mkdir /{investigateboot,investigateroot}
}

rename_local_lvm ()
{
echo "Renaming Local VG"
vgrename -y ${local_vg_list} rescuevg
}

check_local_lvm ()
{
export local_vg_list=`vgs --noheadings -o vg_name| tr -d '   '`
local_vg_number=`vgs --noheadings -o vg_name | wc -l`
if [ ${local_vg_number} -eq 1 ]
	then
		echo "1 VG found, renaming it"
		rename_local_lvm
	else 
		echo "VGs found different than 1, we found ${local_vg_number}"
fi
}

data_os_lvm_check ()
{
export lvm_part=`fdisk -l ${data_disk}| grep -i lvm | awk '{print $1}'`
echo ${lvm_part}
if [ -z ${lvm_part} ]
then
export root_part=`fdisk -l ${data_disk} | grep ^/ |awk '$4 > 60000000{print $1}'`
echo "Your OS partition on the data drive is ${root_part}"
else
export root_part=${lvm_part}
echo "Your OS partition on the data drive is ${lvm_part}"
fi
}

locate_mount_data_boot ()
{
#for i in `fdisk -l \`ls -l /dev/disk/azure/scsi1/lun0 | awk -F/ '{print "/dev/"$9}'\`| grep ^/  | awk '{print $1}'` ; do echo "mkdir -p /tmp$i ; mount $i /tmp$i" ; done | bash
#get partitions on the data disk
export data_parts=`fdisk -l ${data_disk} | grep ^/  | awk '{print $1}'`
echo "Your data partitions are: ${data_parts}"

#create mountpoints for all the data parts
for i in ${data_parts} ; do echo "Creating mountpoint for ${i}" ; mkdir -p /tmp${i}; done

#mount all partitions
for i in ${data_parts} ; do echo "Mounting ${i} on /tmp/${i}" ; mount ${i} /tmp${i}; done
export luksheaderpath=`find /tmp -name osluksheader` 
echo "The luksheader part is ${luksheaderpath}"
export boot_part=`df -h $luksheaderpath | grep ^/ |awk '{print $1}'`
echo "The boot partition on the data disk is ${boot_part}"
}

mount_cmd ()
{
mount_cmd=`mount -o nouuid 2> /dev/null`
if [ $? -gt 0 ]
then
        export mount_cmd="mount"
else
        export mount_cmd="mount -o nouuid"
fi
}

mount_lvm ()
{
echo "Mounting LVM structures found on ${root_part}"
${mount_cmd} /dev/rootvg/rootlv /investigateroot
${mount_cmd} /dev/rootvg/varlv /investigateroot/var/
${mount_cmd} /dev/rootvg/homelv /investigateroot/home
${mount_cmd} /dev/rootvg/optlv /investigateroot/opt
${mount_cmd} /dev/rootvg/usrlv /investigateroot/usr
${mount_cmd} /dev/rootvg/tmplv /investigateroot/tmp
}

unlock_root ()
{
echo "unlocking root with command: cryptsetup luksOpen --key-file /mnt/azure_bek_disk/LinuxPassPhraseFileName --header /investigateboot/luks/osluksheader ${root_part} osencrypt"
cryptsetup luksOpen --key-file /mnt/azure_bek_disk/LinuxPassPhraseFileName --header /investigateboot/luks/osluksheader ${root_part} osencrypt
}

verify_root_unlock ()
{
lsblk -f  | grep osencrypt
if [ $? -gt 0 ]
then
        echo "device osencrypt was not found"
		exit
else
        echo "device osencrypt found"
fi
}

mount_encrypted ()
{
if [ -z ${lvm_part} ]
then
echo "The data disk doesn't have LVM"
echo "Mounting /dev/mapper/osencrypt on /investigateroot"
${mount_cmd} /dev/mapper/osencrypt /investigateroot
else
        sleep 5
        mount_lvm
fi
}

mount_boot ()
{
echo "Unmounting the boot partition ${boot_part} on the data drive from the temp mount"
umount -l ${boot_part}
echo "Mounting the boot partition ${boot_part} on /investigateboot"
${mount_cmd} ${boot_part} /investigateboot/
}

remount_boot ()
{
echo "Unmounting the boot partition ${boot_part} on the data drive from the temp mount"
umount -l ${boot_part}
echo "Mounting the boot partition ${boot_part} on /investigateroot/boot"
${mount_cmd} ${boot_part} /investigateroot/boot
}


duplication_validation
create_mountpoints
get_data_disk
check_local_lvm
data_os_lvm_check
mount_cmd
locate_mount_data_boot
mount_boot
unlock_root
verify_root_unlock
mount_encrypted
remount_boot
