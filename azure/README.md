# Running jobs on Azure

Long jobs (recording bot games, training, self-play) can run on an Azure VM
instead of the laptop. Everything is driven from your laptop with the Azure CLI;
the VM sets itself up, runs one job, keeps its output in Blob Storage and
deallocates itself (stops compute billing) when it finishes.

```
laptop ── azure/deploy.sh ──> Blob Storage  (code, data/, models/, logs/)
                                 ^   |
                       uploads   |   v  downloads inputs, resumes
                                VM (one job; deallocates itself at the end)
```

- **Storage** (resource group `colonist-ai`): one storage account, container
  `colonist`, holding `code/`, `data/<name>/`, `models/<name>/` and `logs/`.
- **VMs** (resource group `colonist-ai-vms`): one VM per job, Ubuntu 24.04. Each
  VM has a managed identity with access only to that storage account and to
  deallocating itself, so no keys or GitHub tokens go onto it. It runs the code
  of the commit you deployed from.

## Once

1. Install the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
   (`brew install azure-cli` on a Mac) and run `az login`. With several
   subscriptions, pick Azure for Students: `az account set -s "Azure for Students"`.
2. Pick a region your subscription allows. Azure for Students limits regions
   by policy; if `deploy.sh` fails with a policy error, try another
   (`LOCATION=westus2`, `centralus`, `northeurope`, ...).

## Jobs

```
azure/deploy.sh datagen ab2 --games 2000      # a trial run: measure speed and cost first
azure/status.sh                               # VM state and the end of each log
azure/deploy.sh datagen ab2 --games 40000     # same name: resumes, adds the rest
azure/deploy.sh train ab2 --epochs 20         # trains on data/ab2 -> models/ab2
azure/fetch.sh models/ab2                     # download the trained net
MODEL=ab2 azure/deploy.sh selfplay sp1 --games 5000   # Phase 3 self-play
azure/teardown.sh                             # delete all VMs; data stays
```

`azure/fetch.sh --up data/ab2` uploads data recorded on the laptop, e.g. to
train on Azure.

Settings: `VM_SIZE` (default `Standard_F4s_v2`, 4 vCPUs), `PRIORITY=Spot`
(much cheaper where the subscription allows it; an evicted VM loses at most
the shards in progress, and redeploying with the same name resumes),
`LOCATION`, and `KEEP_VM=1` to leave the VM running after the job (to `ssh
azureuser@<ip>` and look around; the log is `/var/log/colonist-job.log`).

## Azure for Students

- $100 of credit, no credit card: when it runs out, resources stop rather
  than billing you. Check what is left in the Azure portal (Cost Management or
  the Education hub).
- A small vCPU quota per region; `deploy.sh` prints usage and limit. A VM
  larger than the quota fails to create: use a smaller `VM_SIZE`, or request a
  quota increase in the portal (Quotas).
- A deallocated VM costs only its disk (cents per day). `azure/teardown.sh`
  removes VMs entirely.
- Do the 2,000-game trial first: its log reports games per second, which,
  with the VM's hourly price, says what the 40,000-game run costs.

## Not tested yet

These scripts were written in a cloud container that cannot reach Azure. The
generated VM setup and the on-VM job script were run against stand-ins for
`az` and `azcopy`, but not against Azure itself, so expect a fix or two on the
first real run.
