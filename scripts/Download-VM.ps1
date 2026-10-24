param([Parameter(Mandatory)][string]$Uri,[Parameter(Mandatory)][string]$OutFile)
$ErrorActionPreference='Stop'
if (([Uri]$Uri).Scheme -ne 'https') { throw 'HTTPS download required' }
Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $OutFile
