@echo off
rem Part 2, window C: put load on the service
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera
kubectl apply -f "%~dp0..\k8sxample-autoscaler.yaml"
kubectl scale deploy/load-generator --replicas=5
echo Load is on. Watch windows A and B for one to two minutes.
