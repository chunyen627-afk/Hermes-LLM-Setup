$mac = [byte[]](0xB0,0x25,0xAA,0x48,0x86,0x7A)
$packet = [byte[]](,0xFF * 6) + ($mac * 16)
$udp = New-Object Net.Sockets.UdpClient
$udp.EnableBroadcast = $true
foreach ($target in '192.168.31.255', '255.255.255.255') {
  foreach ($port in 9, 7) {
    [void]$udp.Send($packet, $packet.Length, $target, $port)
  }
}
$udp.Close()
"sent $($packet.Length) bytes"
