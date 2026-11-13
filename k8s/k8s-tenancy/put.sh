#!/bin/bash
# usage: put.sh : copy this directory to ~/tenancy on the k3s server VM (via the homeserver). kubectl then runs there.
cd "$(dirname "$0")"
tar cf - --exclude=put.sh . | ssh openstack-host 'ssh -i /root/k8s-lab/k8s-cp-key -o ConnectTimeout=8 ubuntu@192.168.1.251 "mkdir -p ~/tenancy && tar xf - -C ~/tenancy"'
