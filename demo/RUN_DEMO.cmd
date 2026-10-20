@echo off
rem Guided lab demo. Double-click this file, or run .\demo\RUN_DEMO.cmd, and follow the
rem prompts. It opens two extra windows by itself and tells you what to look at.
title CHIMERA demo - guide
cd /d "%~dp0.."
set KUBECONFIG=%~dp0..\tools\kubeconfig-chimera

echo ======================================================================
echo  CHIMERA lab demo
echo ======================================================================
echo.
echo  STEP 0  Checking that the cluster is up (Docker Desktop must be running)
echo.
kubectl get nodes
if errorlevel 1 (
  echo.
  echo  The cluster is not reachable. Start Docker Desktop, wait for
  echo  "Engine running", then run this file again.
  pause
  exit /b 1
)
kubectl apply -f k8s\example-autoscaler.yaml >nul
kubectl scale deploy/load-generator --replicas=0 >nul
echo.
echo  Starting fresh pods so the restart counter begins at 0 (about 30 seconds)...
kubectl rollout restart deploy/billing-service >nul
kubectl rollout status deploy/billing-service --timeout=180s
echo.
echo  SAY: "This is a real Kubernetes 1.33 cluster. The CHIMERA operator is
echo        running in it and manages a small web service."
echo.
pause

echo.
echo ======================================================================
echo  STEP 1  The controller in simulation (about 20 seconds)
echo ======================================================================
echo  SAY: "Same real trace, same random seed, four controllers."
echo.
set PYTHONPATH=.
python demo_run.py
echo.
echo  POINT AT: section 3 (CHIMERA has the fewest violations; "H only" needs
echo            about 40 percent more CPU) and section 4 (HPA moves replicas,
echo            CHIMERA moves CPU per pod).
echo.
pause

echo.
echo ======================================================================
echo  STEP 2  The operator on the real cluster
echo ======================================================================
echo  Two windows open now. Put them side by side:
echo    "A - operator decisions"   what the operator decides, live
echo    "B - pods and CPU"         the pods, their CPU and restart count
echo.
start "A - operator decisions" cmd /k "%~dp02_operator_log.cmd"
start "B - pods and CPU" cmd /k "%~dp03_watch_pods.cmd"
echo  SAY: "Both pods have 500m of CPU and zero restarts."
echo.
pause

echo.
echo  Putting load on the service...
kubectl scale deploy/load-generator --replicas=5
echo.
echo  WAIT about two minutes. Watch window B: the CPU column goes up on the
echo  SAME pods and RESTARTS stays 0. Window A prints "fast: cpu 0.50 -^> ...".
echo  SAY: "It resized the running pods in place. No restart, no new pod."
echo.
pause

echo.
echo  Removing the load...
kubectl scale deploy/load-generator --replicas=0
echo.
echo  WAIT about one minute. Window B goes back to 500m; window A prints
echo  "fast release".
echo  SAY: "It gives the CPU back as soon as the load is gone."
echo.
pause

echo.
echo ======================================================================
echo  STEP 3  The evidence
echo ======================================================================
echo  Opening the paper. Show Fig. 2 (three real trace sets) and Table VI
echo  with Fig. 4 (the real-cluster comparison with the Kubernetes HPA).
echo.
start "" "%~dp0..\paper\chimera.pdf"
echo  Close windows A and B when you are done (Ctrl+C, then Y).
echo.
pause
