# Run a Power Automate Flow to execute Quicken and export a QIF file and the lot definitions to a XLSX file.

# Start flow
Start-Process "ms-powerautomate:/console/flow/run?environmentid=one-drive-environment-Id&workflowid=e3b01b70-746d-4247-8e15-05c509527634&source=Shortcut&action=close"

# Wait for your flow to finish (adjust the 30 seconds to match your flow's runtime)
Start-Sleep -Seconds 60

# Close the Power Automate Desktop console process
Stop-Process -Name "PAD.Console.Host" -Force -ErrorAction SilentlyContinue

# Copy files to Mac for later processing

$localFile   = "C:\Users\preka\Quicken\Exports\Quicken*"
$remoteUser  = "johnpyrce"
$remoteHost  = "Johns-Mac-Mini.local"
$remotePath  = "repos/Quicken/Exports"
$destination = "${remoteUser}@${remoteHost}:${remotePath}"
# Execute scp
& scp $localFile $destination
