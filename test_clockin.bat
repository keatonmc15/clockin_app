@echo off
REM =========================================
REM Clock-In System Test Script
REM =========================================

REM Make sure you have Flask running in another window

echo.
echo ===============================
echo TEST: Alice Clock-In
echo ===============================
curl -X POST http://127.0.0.1:5000/clock-in ^
  -H "Content-Type: application/json" ^
  -d "{\"qr_code\":\"ALICE123\",\"latitude\":36.15398,\"longitude\":-95.99277}"
echo.
pause

echo.
echo ===============================
echo TEST: Alice Double Clock-In
echo ===============================
curl -X POST http://127.0.0.1:5000/clock-in ^
  -H "Content-Type: application/json" ^
  -d "{\"qr_code\":\"ALICE123\",\"latitude\":36.15398,\"longitude\":-95.99277}"
echo.
pause

echo.
echo ===============================
echo TEST: Bob Clock-In
echo ===============================
curl -X POST http://127.0.0.1:5000/clock-in ^
  -H "Content-Type: application/json" ^
  -d "{\"qr_code\":\"BOB123\",\"latitude\":36.15398,\"longitude\":-95.99277}"
echo.
pause

echo.
echo ===============================
echo TEST: Alice Clock-Out
echo ===============================
curl -X POST http://127.0.0.1:5000/clock-out ^
  -H "Content-Type: application/json" ^
  -d "{\"qr_code\":\"ALICE123\",\"latitude\":36.15398,\"longitude\":-95.99277}"
echo.
pause

echo.
echo ===============================
echo TEST: Bob Clock-Out
echo ===============================
curl -X POST http://127.0.0.1:5000/clock-out ^
  -H "Content-Type: application/json" ^
  -d "{\"qr_code\":\"BOB123\",\"latitude\":36.15398,\"longitude\":-95.99277}"
echo.
pause

echo.
echo TEST COMPLETE
echo ===============================
pause
