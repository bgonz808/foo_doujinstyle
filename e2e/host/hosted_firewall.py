"""Hosted pilot defense in depth, not an independent egress boundary."""
import json
import os
import subprocess
from e2e.host.runner import QualificationError

def constrain_host(exe, run_id):
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise QualificationError("Not a GitHub-hosted runner")
    env={**os.environ,"DOUJIN_FIREWALL_EXE":str(exe),"DOUJIN_FIREWALL_ID":"DoujinE2E-"+run_id}
    script=r"""
$ErrorActionPreference='Stop'
if (@(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object {$_.Enabled -ne 'True'}).Count) { throw 'Firewall profile disabled' }
$addresses=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::/0')
New-NetFirewallRule -Name $env:DOUJIN_FIREWALL_ID -DisplayName $env:DOUJIN_FIREWALL_ID -Direction Outbound -Action Block -Enabled True -Profile Any -Program $env:DOUJIN_FIREWALL_EXE -RemoteAddress $addresses | Out-Null
$r=Get-NetFirewallRule -PolicyStore ActiveStore -Name $env:DOUJIN_FIREWALL_ID
$p=$r | Get-NetFirewallApplicationFilter
$a=$r | Get-NetFirewallAddressFilter
if ($r.Enabled -ne 'True' -or $r.Action -ne 'Block' -or $r.Direction -ne 'Outbound' -or $p.Program -ne $env:DOUJIN_FIREWALL_EXE) {throw 'Rule readback mismatch'}
if (@(Compare-Object ($addresses | Sort-Object) ($a.RemoteAddress | Sort-Object)).Count) {throw 'Address readback mismatch'}
@{rule_verified=$true; remote_addresses=@($a.RemoteAddress); scope='exact_foobar2000_executable_only'; independent_boundary=$false} | ConvertTo-Json -Compress
"""
    p=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",script],env=env,capture_output=True,text=True,timeout=45)
    if p.returncode:
        raise QualificationError("Hosted firewall setup/readback failed: "+p.stderr[:1000])
    evidence=json.loads(p.stdout)
    if evidence.get("rule_verified") is not True:
        raise QualificationError("Hosted firewall evidence missing")
    return evidence
