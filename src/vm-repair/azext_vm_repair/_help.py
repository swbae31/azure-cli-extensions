# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.help_files import helps

helps['vm repair'] = """
    type: group
    short-summary: Auto repair commands to fix VMs.
    long-summary: |
        VM repair scripts will enable Azure users to self-repair non-bootable VMs by copying the source VM's OS disk and attaching it to a newly created repair VM.
"""

helps['vm repair create'] = """
    type: command
    short-summary: Create a new repair VM and attach the source VM's copied OS disk as a data disk.
    examples:
        - name: Create a repair VM
          text: >
            az vm repair create -g MyResourceGroup -n myVM --verbose
        - name: Create a repair VM and set the VM authentication
          text: >
            az vm repair create -g MyResourceGroup -n myVM --repair-username username --repair-password password!234 --verbose
"""

helps['vm repair restore'] = """
    type: command
    short-summary: Replace source VM's OS disk with data disk from repair VM.
    examples:
        - name: Restore from the repair VM, command will auto-search for repair-vm
          text: >
            az vm repair restore -g MyResourceGroup -n MyVM --verbose
        - name: Restore from the repair VM, specify the disk to restore
          text: >
            az vm repair restore -g MyResourceGroup -n MyVM --disk-name MyDiskCopy --verbose
"""

helps['vm repair run'] = """
    type: command
    short-summary: Run verified scripts on the repair-vm. 'az vm repair run-list' to view available scripts.
    examples:
        - name: Run the script with <run-id> on the repair VM.
          text: >
            az vm repair run -g MyResourceGroup -n MySourceWinVM --run-id win-hello-world --run-on-repair --verbose
        - name: Run a script with parameters on the repair VM.
          text: >
            az vm repair run -g MyResourceGroup -n MySourceWinVM --run-id win-hello-world --parameters hello=hi world=earth --run-on-repair --verbose
        - name: Run a custom script on the repair VM.
          text: >
            az vm repair run -g MyResourceGroup -n MySourceWinVM --custom_run_file ./file.ps1 --run-on-repair --verbose
"""

helps['vm repair run-list'] = """
    type: command
    short-summary: List available scripts. Located https://github.com/Azure/repair-script-library.
    examples:
        - name: List scripts
          text: >
            az vm repair run-list --verbose
        - name: List windows scripts only.
          text: >
            az vm repair run-list --query "[?starts_with(id, 'win')]"
        - name: List scripts with test in its description.
          text: >
            az vm repair run-list --query "[?contains(description, 'test')]"
"""