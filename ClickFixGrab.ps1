# ==============================================================================
# 🚀 PowerShell Web-Scraper: ClickFix-Datenextraktion (Vollständig & Final)
# ==============================================================================

#region Skript-Definitionen

# Basis-URL (Root der Domain/des Repos), um absolute Pfade korrekt aufzulösen
$BaseUrl = "https://mhaggis.github.io"
# URL der Seite, die die Links zu den Reports enthält
$IndexUrl = "$BaseUrl/ClickGrab/reports.html"

# Regex-Muster für die Links auf der Hauptseite
$LinkRegex = 'href=\"(?<Link>\/ClickGrab\/reports\/20[0-9]{2}\-[0-9]{2}\-[0-9]{2}\.html)\"'

# Dateipfade für die Exportdateien
$UrlsFile = "ClickFix-URLS.txt"
$KeywordsFile = "Keywords.txt"
$CommandsFile = "ClickFix-Commands.txt"

# HashSets zur Speicherung der gesammelten Werte und zur Vermeidung von Duplikaten
$AllSiteUrls = [System.Collections.Generic.HashSet[string]]::new()
$AllKeywords = [System.Collections.Generic.HashSet[string]]::new()
$AllCommands = [System.Collections.Generic.HashSet[string]]::new()

#endregion

# ==============================================================================
# 🛠️ Funktion zum Parsen einer einzelnen Report-Seite
# ==============================================================================

function Parse-ReportPage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ReportUrl
    )

    Write-Host "Verarbeite: ${ReportUrl}" -ForegroundColor Cyan

    try {
        # Seite herunterladen
        $ReportContent = (Invoke-WebRequest -Uri $ReportUrl -UseBasicParsing).Content

        # 1. Site-URL parsen: <div class="site-url">...</div> 
        $UrlPattern = 'class=\"site-url\">(?<Url>[^<]+)<\/div>'
        foreach ($Match in [regex]::Matches($ReportContent, $UrlPattern)) {
            $SiteUrl = $Match.Groups['Url'].Value.Trim()
            if (-not [string]::IsNullOrEmpty($SiteUrl)) { [void]$AllSiteUrls.Add($SiteUrl) }
        }

        # 2. Keywords parsen: unter <h3>Keyword Categories</h3>, Werte in <code>...</code>
        $KeywordSectionPattern = '<h3>Keyword Categories<\/h3>.*?<\/ul>'
        $KeywordSectionMatch = [regex]::Match($ReportContent, $KeywordSectionPattern, 'Singleline')
        
        if ($KeywordSectionMatch.Success) {
            $CodeMatches = [regex]::Matches($KeywordSectionMatch.Value, '<code>(?<Keyword>[^<]+)<\/code>')
            foreach ($Match in $CodeMatches) {
                $Keyword = $Match.Groups['Keyword'].Value.Trim()
                if (-not [string]::IsNullOrEmpty($Keyword)) { [void]$AllKeywords.Add($Keyword) }
            }
        }

        # 3. System Commands parsen (KORRIGIERT: Extrahiert den gesamten <li> Inhalt)
        # Erfasst den Inhalt von <h4>System Commands</h4> bis zum nächsten <h3>, <h4> oder Dateiende.
        $CommandSectionPattern = '<h4>System Commands<\/h4>(?<Section>.*?)($|(?=<(h3|h4)))'
        $CommandSectionMatch = [regex]::Match($ReportContent, $CommandSectionPattern, 'Singleline')
        
        if ($CommandSectionMatch.Success) {
            $CommandSection = $CommandSectionMatch.Groups['Section'].Value
            
            # NEUER ANSATZ: Extrahiere den gesamten Inhalt jedes <li>-Tags.
            $ListItemPattern = '<li>(?<CommandItem>.*?)<\/li>'
            $ListItemMatches = [regex]::Matches($CommandSection, $ListItemPattern, 'Singleline')

            foreach ($Match in $ListItemMatches) {
                # Hole den gesamten <li>-Inhalt
                $Command = $Match.Groups['CommandItem'].Value
                
                # Entferne ALLE <code>-Tags, um den vollständigen Befehl zu erhalten
                $Command = $Command -replace '<\/?code>', ''
                $Command = $Command.Trim()
                
                if (-not [string]::IsNullOrEmpty($Command)) { [void]$AllCommands.Add($Command) }
            }
        }
    }
    catch {
        # ParserError-Fix: ${}-Notation zur eindeutigen Delimitierung der Variablen
        Write-Warning "Fehler beim Abrufen/Verarbeiten von ${ReportUrl}: " -NoNewline
        Write-Warning $_.Exception.Message
    }
}

# ==============================================================================
# 🏃 Hauptlogik
# ==============================================================================

Write-Host "Starte Skript..." -ForegroundColor Green

#region Hauptseite abrufen und Links sammeln

try {
    Write-Host "Lade Indexseite: $IndexUrl"
    $HomePageContent = (Invoke-WebRequest -Uri $IndexUrl -UseBasicParsing).Content

    $LinkMatches = [regex]::Matches($HomePageContent, $LinkRegex)
    
    if ($LinkMatches.Count -eq 0) {
        Write-Warning "Keine Links auf der Indexseite gefunden, die dem Muster entsprechen."
        exit
    }

    $ReportUrlsToProcess = @()
    # Eindeutige URLs zusammenbauen und speichern
    foreach ($Match in $LinkMatches) {
        $RelativeLink = $Match.Groups['Link'].Value
        $FullUrl = $BaseUrl + $RelativeLink 
        
        if ($FullUrl -notin $ReportUrlsToProcess) {
            $ReportUrlsToProcess += $FullUrl
        }
    }
    
    # Gefundene URLs ausgeben
    Write-Host "--- Gefundene eindeutige Report-Links (${ReportUrlsToProcess.Count}) ---" -ForegroundColor Yellow
    $ReportUrlsToProcess | ForEach-Object { Write-Host $_ }
    Write-Host "--------------------------------------------------------" -ForegroundColor Yellow

}
catch {
    Write-Error "Fehler beim Abrufen der Indexseite: $($_.Exception.Message)"
    exit
}

#endregion

#region Report-Seiten parsen

Write-Host "Starte Verarbeitung der $($ReportUrlsToProcess.Count) eindeutigen Report-Seiten..." -ForegroundColor Yellow
foreach ($Url in $ReportUrlsToProcess) {
    Parse-ReportPage -ReportUrl $Url
}

#endregion

#region Export der eindeutigen Daten

Write-Host "Exportiere die gesammelten, eindeutigen Daten in Textdateien..." -ForegroundColor Green

# Export Site URLs
$AllSiteUrls | Sort-Object | Out-File -FilePath $UrlsFile -Encoding UTF8
Write-Host "✅ $([string]::Format('{0:N0}', $AllSiteUrls.Count)) eindeutige Site-URLs in '$UrlsFile' geschrieben."

# Export Keywords
$AllKeywords | Sort-Object | Out-File -FilePath $KeywordsFile -Encoding UTF8
Write-Host "✅ $([string]::Format('{0:N0}', $AllKeywords.Count)) eindeutige Keywords in '$KeywordsFile' geschrieben."

# Export Commands
$AllCommands | Sort-Object | Out-File -FilePath $CommandsFile -Encoding UTF8
Write-Host "✅ $([string]::Format('{0:N0}', $AllCommands.Count)) eindeutige Commands in '$CommandsFile' geschrieben."

Write-Host "=======================================================" -ForegroundColor DarkGreen
Write-Host "               Skript erfolgreich beendet.             " -ForegroundColor DarkGreen
Write-Host "=======================================================" -ForegroundColor DarkGreen

#endregion