' Starts the Bluetooth thermometer gateway hidden (pythonw), at logon from the Startup folder.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\pjunm\dev\thermo-gateway"
sh.Run """C:\Users\pjunm\AppData\Local\Programs\Python\Python311\pythonw.exe"" ""C:\Users\pjunm\dev\thermo-gateway\thermo_gateway.py"" --config ""C:\Users\pjunm\dev\thermo-gateway\thermo-gateway.json""", 0, False
