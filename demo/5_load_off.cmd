@echo off
rem Part 2, window C: remove the load
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera
kubectl scale deploy/load-generator --replicas=0
echo Load is off. CPU returns to 500m within about two minutes.
