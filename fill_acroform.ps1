$base = 'http://127.0.0.1:5000'
$formId = 'form956'

# Same dummy payload as the prior fill — covers every field type
$body = @{
  agent_title        = 'mr'
  agent_title_other  = ''
  is_new_application = 'Yes'
  preferred_communication = 'Yes'
  assistance_type    = 'Legal'
  another_migration_agent = 'No'
  exemption_reason   = 'sponsor'
  agent_family_name  = 'Smith'
  agent_given_names  = 'Jane Elizabeth'
  agent_dob          = '01/01/1980'
  agent_org_name     = 'Smith Migration Services Pty Ltd'
  agent_resadd_str   = '1 Pitt Street'
  agent_resadd_sub   = 'Sydney'
  agent_resadd_cntry = 'Australia'
  agent_resadd_pc    = '2000'
  agent_postal_str   = '1 Pitt Street'
  agent_postal_sub   = 'Sydney'
  agent_postal_cntry = 'Australia'
  agent_postal_pc    = '2000'
  agent_off_ph_cc    = '61'
  agent_off_ph_ac    = '2'
  agent_off_ph       = '98765432'
  agent_mob          = '0412345678'
  agent_email        = 'jane.smith@example.com'
  agent_marn         = '1234567'
  agent_lpn          = '9876543'

  client_role        = 'visa'
  assistance_category = 'Application'
  not_yet_decided    = $false
  also_assisting_another = 'No'
  client_dob         = '15/06/1990'
  client_org_name    = 'Acme Pty Ltd'
  client_resadd_str  = '42 George Street'
  client_resadd_sub  = 'Sydney'
  client_resadd_cntry= 'Australia'
  client_resadd_pc   = '2000'
  client_off_ph_cc   = '61'
  client_off_ph_ac   = '2'
  client_off_ph      = '12345678'
  client_mob         = '0498765432'
  client_diac_id     = 'DIAC-0001'
  application_type   = 'Skilled visa'
  date_lodged        = '01/05/2026'

  people = @(
    @{ family = 'Doe'; given = 'John'   }
    @{ family = 'Doe'; given = 'Jane'   }
    @{ family = 'Doe'; given = 'Junior' }
  )

  also_assisting_in_ending = 'Yes'
  ending_this_appointment  = 'Yes'
  communicated_ending      = 'Yes'

  agent_declarations_agreed  = $true
  client_declarations_agreed = $true
  agent_declaration_date = '06/06/2026'
  client_declaration_date = '06/06/2026'
} | ConvertTo-Json -Depth 8

Write-Host '=== Health check ===' -ForegroundColor Cyan
$h = Invoke-RestMethod -Uri "$base/health" -Method Get
$h | ConvertTo-Json

Write-Host ''
Write-Host '=== POST /forms/form956/fill (1st call, expect cache miss) ===' -ForegroundColor Cyan
$tmp1 = New-TemporaryFile
$sw1 = [System.Diagnostics.Stopwatch]::StartNew()
$hr1 = Invoke-WebRequest -Uri "$base/forms/$formId/fill" -Method Post -Body $body -ContentType 'application/json' -OutFile $tmp1.FullName -PassThru -UseBasicParsing
$sw1.Stop()
"  Status:        $($hr1.StatusCode)"
"  X-Cache:       $($hr1.Headers['X-Cache'])"
"  X-Cache-Key:   $($hr1.Headers['X-Cache-Key'])"
"  X-Request-Id:  $($hr1.Headers['X-Request-Id'])"
"  Size:          $((Get-Item $tmp1).Length) bytes"
"  Latency:       $($sw1.ElapsedMilliseconds) ms"
$key1 = $hr1.Headers['X-Cache-Key']

Write-Host ''
Write-Host '=== POST /forms/form956/fill (2nd call, same payload, expect cache HIT) ===' -ForegroundColor Cyan
$tmp2 = New-TemporaryFile
$sw2 = [System.Diagnostics.Stopwatch]::StartNew()
$hr2 = Invoke-WebRequest -Uri "$base/forms/$formId/fill" -Method Post -Body $body -ContentType 'application/json' -OutFile $tmp2.FullName -PassThru -UseBasicParsing
$sw2.Stop()
"  Status:        $($hr2.StatusCode)"
"  X-Cache:       $($hr2.Headers['X-Cache'])"
"  X-Cache-Key:   $($hr2.Headers['X-Cache-Key'])"
"  Size:          $((Get-Item $tmp2).Length) bytes"
"  Latency:       $($sw2.ElapsedMilliseconds) ms"
$key2 = $hr2.Headers['X-Cache-Key']
if ($key1 -eq $key2) { "  Same cache key: YES" } else { "  Same cache key: NO ($key1 vs $key2)" }

Write-Host ''
Write-Host '=== GET /forms/form956/fill?key=<key> (cache re-fetch) ===' -ForegroundColor Cyan
$tmp3 = New-TemporaryFile
$hr3 = Invoke-WebRequest -Uri "$base/forms/$formId/fill?key=$key1" -Method Get -OutFile $tmp3.FullName -PassThru -UseBasicParsing
"  Status:  $($hr3.StatusCode)"
"  Size:    $((Get-Item $tmp3).Length) bytes"

Write-Host ''
Write-Host '=== Validation error: bad MARN ===' -ForegroundColor Cyan
$bad = ($body -replace '"agent_marn":\s*"1234567"', '"agent_marn": "abc"')
try {
  $r = Invoke-WebRequest -Uri "$base/forms/$formId/fill" -Method Post -Body $bad -ContentType 'application/json' -PassThru -UseBasicParsing
  "  UNEXPECTED 200"
} catch {
  $err = $_.Exception.Response
  "  Status:  $($err.StatusCode.value__)"
  if ($_.Exception.Response -is [System.Net.HttpWebResponse]) {
    $bodyStream = $_.Exception.Response.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($bodyStream, [System.Text.Encoding]::UTF8)
    "  Body:    $($reader.ReadToEnd().Trim())"
    $reader.Close()
  } else {
    "  Body:    $($_.Exception.Message)"
  }
}

Write-Host ''
Write-Host '=== Validation error: missing required field ===' -ForegroundColor Cyan
$bad2 = ($body -replace '"agent_family_name":\s*"Smith"', '"agent_family_name": ""')
try {
  $r = Invoke-WebRequest -Uri "$base/forms/$formId/fill" -Method Post -Body $bad2 -ContentType 'application/json' -PassThru -UseBasicParsing
  "  UNEXPECTED 200"
} catch {
  $err = $_.Exception.Response
  "  Status:  $($err.StatusCode.value__)"
  if ($_.Exception.Response -is [System.Net.HttpWebResponse]) {
    $bodyStream = $_.Exception.Response.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($bodyStream, [System.Text.Encoding]::UTF8)
    "  Body:    $($reader.ReadToEnd().Trim())"
    $reader.Close()
  } else {
    "  Body:    $($_.Exception.Message)"
  }
}

Write-Host ''
Write-Host '=== Save sample filled PDF ===' -ForegroundColor Cyan
$out = "C:\Users\Administrator\OneDrive - Office 365\Desktop\Pdf_readerForm950\956_acroform_filled.pdf"
Copy-Item $tmp1.FullName $out -Force
$f = Get-Item $out
"  Saved:  $($f.FullName)"
"  Size:   $($f.Length) bytes ($([math]::Round($f.Length/1KB, 1)) KB)"

Write-Host ''
Write-Host '=== Verify ticks landed on the right widgets (read-back via /extract) ===' -ForegroundColor Cyan
$json = (Invoke-RestMethod -Uri "$base/forms/$formId/extract?key=$key1") | ConvertTo-Json -Depth 6
$obj  = $json | ConvertFrom-Json
"  Total widgets with values: $(@($obj.PSObject.Properties).Count)"
''
"  Radio ticks (should match intended choices):"
@($obj.PSObject.Properties) | Where-Object { $_.Value -in @('mr','mrs','miss','ms','Yes','No','Legal','reg','exampt','visa','sponsor','Application','Cancellation','Specific','IAAAS','close','nominator','diplom','parlia','public','sponsor') } | ForEach-Object { "    $($_.Name) = $($_.Value)" }

"  Declaration ticks (should all be 'on'):"
@($obj.PSObject.Properties) | Where-Object { $_.Name -like '*.dec *' -and $_.Name -notlike '*date' } | ForEach-Object { "    $($_.Name) = $($_.Value)" }
