@echo off
rem CHIMERA live demonstration. Run from the project folder or double-click.
rem Talking points for the presenter are in DEMO.md, not on screen.
title CHIMERA - live demonstration
cd /d "%~dp0.."
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera

echo ======================================================================
echo  CHIMERA: joint horizontal and vertical pod autoscaling
echo ======================================================================
echo.
echo  [0] Kubernetes cluster
echo.
kubectl get nodes
if errorlevel 1 (
  echo.
  echo  The cluster is not reachable. Start Docker Desktop and run this again.
  pause
  exit /b 1
)
kubectl apply -f k8s\example-autoscaler.yaml >nul
kubectl scale deploy/load-generator --replicas=0 >nul
kubectl rollout restart deploy/billing-service >nul
kubectl rollout status deploy/billing-service --timeout=180s >nul
echo.
pause

echo.
echo ======================================================================
echo  [1] Simulation: CHIMERA, AAPA and HPA on the same real trace
echo ======================================================================
echo.
set PYTHONPATH=.
python demo_run.py
echo.
pause

echo.
echo ======================================================================
echo  [2] Live cluster: in-place CPU resize under load
echo ======================================================================
start "A - operator decisions" cmd /k "%~dp02_operator_log.cmd"
start "B - pods and CPU" cmd /k "%~dp03_watch_pods.cmd"
echo.
pause
echo.
echo  Load applied.
kubectl scale deploy/load-generator --replicas=5 >nul
echo.
pause

echo.
echo ======================================================================
echo  [3] Live cluster: adding a pod when resizing is not enough
echo      (CPU per pod capped at 1 core, load increased)
echo ======================================================================
kubectl patch chimeraautoscaler billing-service-scaler --type merge -p "{\"spec\":{\"maxCPU\":\"1000m\"}}" >nul
kubectl scale deploy/load-generator --replicas=8 >nul
echo.
pause

echo.
echo ======================================================================
echo  [4] Live cluster: load removed, CPU released
echo ======================================================================
kubectl scale deploy/load-generator --replicas=0 >nul
kubectl patch chimeraautoscaler billing-service-scaler --type merge -p "{\"spec\":{\"maxCPU\":\"2000m\"}}" >nul
echo.
pause

echo.
echo ======================================================================
echo  [5] Results
echo ======================================================================
start "" "%~dp0..\paper\chimera.pdf"
echo.
pause
