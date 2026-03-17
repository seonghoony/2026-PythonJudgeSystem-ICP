// Replace with your server's IP or Domain
const url = "https://www.snsec.net/dashboard/icp-spring26/api/widget"

let req = new Request(url)
let data = await req.loadJSON()

let widget = new ListWidget()
widget.backgroundColor = new Color("#1b1b1f")
widget.setPadding(12, 12, 12, 12)

let title = widget.addText("💻 프로그래밍입문")
title.textColor = Color.yellow()
title.font = Font.boldSystemFont(14)
widget.addSpacer(8)

if (data && data.active_assignments) {
  // Aggregate data into a matrix: { "3-1": {"001": "1s", "003": "5s"}, ... }
  let tableData = {}
  let uniqueSections = []
  
  for (let item of data.active_assignments) {
    let name = item.name
    let sec = item.section
    
    if (!uniqueSections.includes(sec)) {
      uniqueSections.push(sec)
    }
    
    if (!(name in tableData)) {
      tableData[name] = {}
    }
    tableData[name][sec] = item.time_ago
  }
  
  uniqueSections.sort() // "001", "003"
  
  // -- Header row --
  let headerRow = widget.addStack()
  
  // Blank column for assignment names (top-left empty space)
  let hNameCol = headerRow.addStack()
  hNameCol.size = new Size(30, 12)
  headerRow.addSpacer()
  
  // Section headers ("001" | "003")
  for (let sec of uniqueSections) {
    let sCol = headerRow.addStack()
    sCol.size = new Size(30, 12)
    sCol.addSpacer() // Pushes text to the right
    let sText = sCol.addText(sec)
    sText.textColor = new Color("#0a84ff")
    sText.font = Font.boldSystemFont(10)
    headerRow.addSpacer()
  }
  widget.addSpacer(2)
  
  // -- Data rows --
  let rowNames = Object.keys(tableData).sort()
  for (let name of rowNames) {
    let row = widget.addStack()
    
    // Assignment name column ("3-1")
    let nameCol = row.addStack()
    nameCol.size = new Size(30, 12)
    let nText = nameCol.addText(name)
    nText.textColor = Color.white()
    nText.font = Font.boldSystemFont(11)
    row.addSpacer()
    
    // Values columns ("Ns")
    for (let sec of uniqueSections) {
      let vCol = row.addStack()
      vCol.size = new Size(30, 12)
      vCol.addSpacer() // Pushes text to the right
      
      let valText = tableData[name][sec]
      if (!valText) {
          valText = "-"
      }
      
      let vText = vCol.addText(valText)
      vText.textColor = new Color("#30d158")
      vText.font = Font.systemFont(11)
      row.addSpacer()
    }
    widget.addSpacer(2)
  }
}

widget.addSpacer()

let serverTime = "Unknown"
if (data && data.server_time) {
  let parts = data.server_time.split(" ")
  serverTime = parts.length > 1 ? parts[1] : data.server_time
}

let footerText = "Last Update: " + serverTime
let footer = widget.addText(footerText)
footer.textColor = new Color("#48484a")
footer.font = Font.systemFont(9)

if (config.runsInWidget) {
  Script.setWidget(widget)
} else {
  widget.presentSmall() 
}

Script.complete()
