@echo off
rem Part 2, window B: the service pods, their CPU and restart count (Ctrl+C to stop)
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera
kubectl get pods -l app=billing-service -w -o "custom-columns=NAME:.metadata.name,CPU:.spec.containers[0].resources.limits.cpu,RESTARTS:.status.containerStatuses[0].restartCount"
