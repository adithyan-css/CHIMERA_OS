@echo off
rem Check before the review: cluster, operator, autoscaler
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera
kubectl get nodes
kubectl get pods -A
kubectl get chas
