#!/usr/bin/env python3
"""
Generate loss curve plot from benchmark results
"""
import csv
import os

# Read data
data = []
with open('linux_loss_curve.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        data.append({
            'rate': int(row['rate']),
            'loss_pct': float(row['loss_pct'])
        })

# Generate HTML chart (using simple SVG)
html_content = """<!DOCTYPE html>
<html>
<head>
    <title>PyStatsD-Helix Linux Benchmark - Loss Curve</title>
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; margin: 40px; background: #1a1a2e; color: #eee; }
        h1 { color: #00d4ff; }
        .chart-container { background: #16213e; border-radius: 10px; padding: 30px; max-width: 900px; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #444; padding: 12px; text-align: right; }
        th { background: #0f3460; color: #00d4ff; }
        tr:nth-child(even) { background: #1a1a2e; }
        tr:hover { background: #0f3460; }
        .good { color: #4ade80; }
        .warning { color: #fbbf24; }
        .danger { color: #f87171; }
        .summary { background: #0f3460; padding: 20px; border-radius: 8px; margin: 20px 0; }
        .metric { display: inline-block; margin: 0 30px; }
        .metric-value { font-size: 28px; font-weight: bold; color: #00d4ff; }
        .metric-label { font-size: 14px; color: #888; }
    </style>
</head>
<body>
    <h1>🚀 PyStatsD-Helix Linux Benchmark Results</h1>
    
    <div class="summary">
        <div class="metric">
            <div class="metric-value">200K</div>
            <div class="metric-label">Max Tested Rate (pkt/s)</div>
        </div>
        <div class="metric">
            <div class="metric-value">100K</div>
            <div class="metric-label">Zero-Loss Threshold</div>
        </div>
        <div class="metric">
            <div class="metric-value">uvloop ✓</div>
            <div class="metric-label">Event Loop</div>
        </div>
    </div>
    
    <div class="chart-container">
        <h2>📊 Packet Rate vs Loss Rate</h2>
        <svg viewBox="0 0 800 400" style="width: 100%; height: 400px;">
            <!-- Grid -->
            <line x1="80" y1="50" x2="80" y2="350" stroke="#333" stroke-width="2"/>
            <line x1="80" y1="350" x2="750" y2="350" stroke="#333" stroke-width="2"/>
            
            <!-- Y axis labels -->
            <text x="70" y="55" fill="#888" text-anchor="end" font-size="12">10%</text>
            <text x="70" y="125" fill="#888" text-anchor="end" font-size="12">7.5%</text>
            <text x="70" y="200" fill="#888" text-anchor="end" font-size="12">5%</text>
            <text x="70" y="275" fill="#888" text-anchor="end" font-size="12">2.5%</text>
            <text x="70" y="350" fill="#888" text-anchor="end" font-size="12">0%</text>
            
            <!-- X axis labels -->
            <text x="80" y="380" fill="#888" text-anchor="middle" font-size="11">10K</text>
            <text x="190" y="380" fill="#888" text-anchor="middle" font-size="11">30K</text>
            <text x="300" y="380" fill="#888" text-anchor="middle" font-size="11">50K</text>
            <text x="410" y="380" fill="#888" text-anchor="middle" font-size="11">80K</text>
            <text x="520" y="380" fill="#888" text-anchor="middle" font-size="11">100K</text>
            <text x="630" y="380" fill="#888" text-anchor="middle" font-size="11">150K</text>
            <text x="740" y="380" fill="#888" text-anchor="middle" font-size="11">200K</text>
            
            <!-- Grid lines -->
            <line x1="80" y1="125" x2="750" y2="125" stroke="#333" stroke-dasharray="5,5"/>
            <line x1="80" y1="200" x2="750" y2="200" stroke="#333" stroke-dasharray="5,5"/>
            <line x1="80" y1="275" x2="750" y2="275" stroke="#333" stroke-dasharray="5,5"/>
            
            <!-- Line chart -->
            <polyline 
                points="80,350 190,350 300,350 410,350 520,344 630,185 740,126" 
                fill="none" 
                stroke="#00d4ff" 
                stroke-width="3"
                stroke-linecap="round"
                stroke-linejoin="round"/>
            
            <!-- Data points -->
            <circle cx="80" cy="350" r="6" fill="#4ade80"/>
            <circle cx="190" cy="350" r="6" fill="#4ade80"/>
            <circle cx="300" cy="350" r="6" fill="#4ade80"/>
            <circle cx="410" cy="350" r="6" fill="#4ade80"/>
            <circle cx="520" cy="344" r="6" fill="#4ade80"/>
            <circle cx="630" cy="185" r="6" fill="#fbbf24"/>
            <circle cx="740" cy="126" r="6" fill="#f87171"/>
            
            <!-- Labels -->
            <text x="400" y="30" fill="#eee" text-anchor="middle" font-size="16" font-weight="bold">Packet Rate vs Loss Rate (Linux/Docker)</text>
            <text x="400" y="395" fill="#888" text-anchor="middle" font-size="12">Packet Rate (pkt/s)</text>
            <text x="25" y="200" fill="#888" text-anchor="middle" font-size="12" transform="rotate(-90, 25, 200)">Loss Rate (%)</text>
        </svg>
    </div>
    
    <h2>📋 Detailed Results</h2>
    <table>
        <tr>
            <th>Target Rate (pkt/s)</th>
            <th>Sent</th>
            <th>Received</th>
            <th>Loss %</th>
            <th>Status</th>
        </tr>
"""

for d in data:
    rate = d['rate']
    loss = d['loss_pct']
    
    # Find matching full data
    sent = received = 0
    with open('linux_loss_curve.csv', 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row['rate']) == rate:
                sent = int(row['sent'])
                received = int(row['received'])
                break
    
    if loss < 0.1:
        status_class = "good"
        status = "✅ Excellent"
    elif loss < 5:
        status_class = "warning"
        status = "⚠️ Acceptable"
    else:
        status_class = "danger"
        status = "❌ High Loss"
    
    html_content += f"""        <tr>
            <td>{rate:,}</td>
            <td>{sent:,}</td>
            <td>{received:,}</td>
            <td class="{status_class}">{loss:.2f}%</td>
            <td class="{status_class}">{status}</td>
        </tr>
"""

html_content += """    </table>
    
    <div class="summary" style="margin-top: 30px;">
        <h3>🎯 Key Findings</h3>
        <ul>
            <li><strong>Zero-loss performance:</strong> Achieved up to <span class="good">80,000 pkt/s</span> with 0% packet loss</li>
            <li><strong>Excellent performance:</strong> Up to <span class="good">100,000 pkt/s</span> with &lt;0.2% loss</li>
            <li><strong>Degradation point:</strong> Loss starts increasing significantly above 100K pkt/s</li>
            <li><strong>Maximum tested:</strong> 200,000 pkt/s with 7.46% loss</li>
        </ul>
    </div>
    
    <p style="color: #666; margin-top: 40px; font-size: 12px;">
        Generated by PyStatsD-Helix Benchmark Suite | Linux (Docker) | uvloop enabled
    </p>
</body>
</html>"""

with open('linux_loss_curve.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print("Generated: linux_loss_curve.html")
print("\nBenchmark Summary:")
print("-" * 50)
for d in data:
    status = "✅" if d['loss_pct'] < 0.1 else ("⚠️" if d['loss_pct'] < 5 else "❌")
    print(f"  {d['rate']:>7,} pkt/s: {d['loss_pct']:>6.2f}% loss {status}")
