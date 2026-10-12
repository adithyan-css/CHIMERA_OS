@echo off
rem Part 2, window A: the operator decisions as they happen (Ctrl+C to stop)
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera
kubectl -n kube-system logs -f --tail=5 deploy/chimera-controller
