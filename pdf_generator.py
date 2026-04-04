"""
pdf_generator.py
Generates professional PDF engineering reports for TELECOM TOWER POWER.
Uses ReportLab for tables/layout and Matplotlib for the Fresnel zone plot.
"""

import io
import math
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server environments
import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT


# Inline FSPL to avoid circular import with telecom_tower_power_api
def _free_space_path_loss(d_km: float, f_hz: float) -> float:
    d_m = d_km * 1000
    return 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

# ----------------------------------------------------------------------
# Plot generation: terrain + Fresnel zone (first zone)
# ----------------------------------------------------------------------

def generate_fresnel_plot(
    terrain_profile: List[float],
    distance_km: float,
    tx_height: float,
    rx_height: float,
    frequency_hz: float,
    num_points: int = 100
) -> io.BytesIO:
    """
    Create a matplotlib figure showing:
      - Ground elevation along path (ASL)
      - Straight line between TX and RX antennas (ASL)
      - First Fresnel zone (radius around line-of-sight)
    tx_height / rx_height are AGL (above ground level).
    Returns BytesIO buffer with PNG image.
    """
    # Create distance array (km)
    distances = np.linspace(0, distance_km, num_points)
    # Interpolate terrain profile (if profile has fewer points)
    if len(terrain_profile) != num_points:
        x_orig = np.linspace(0, distance_km, len(terrain_profile))
        ground = np.interp(distances, x_orig, terrain_profile)
    else:
        ground = np.array(terrain_profile)

    # Convert AGL heights to ASL by adding ground elevation at each endpoint
    tx_asl = ground[0] + tx_height
    rx_asl = ground[-1] + rx_height

    # Line-of-sight height at each distance (ASL)
    los_height = tx_asl + (rx_asl - tx_asl) * (distances / distance_km)

    # First Fresnel zone radius at each point (meters)
    fresnel_radius = np.zeros_like(distances)
    c = 299792458
    for i, d in enumerate(distances):
        d1 = d * 1000
        d2 = (distance_km - d) * 1000
        if d1 > 0 and d2 > 0:
            fresnel_radius[i] = math.sqrt((c * d1 * d2) / (frequency_hz * (d1 + d2)))
        else:
            fresnel_radius[i] = 0

    # Upper and lower boundaries of first Fresnel zone (relative to LOS)
    upper_fresnel = los_height + fresnel_radius
    lower_fresnel = los_height - fresnel_radius

    # Create plot
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.fill_between(distances, ground, ground.min() - 20, color='sandybrown', alpha=0.6, label='Terrain')
    ax.plot(distances, ground, 'brown', linewidth=1)
    ax.plot(distances, los_height, 'b--', linewidth=2, label='Line-of-sight')
    ax.fill_between(distances, lower_fresnel, upper_fresnel, color='green', alpha=0.3, label='1st Fresnel Zone')
    ax.plot(0, tx_asl, '^', color='red', markersize=10, zorder=5, label='Tower')
    ax.plot(distance_km, rx_asl, 'v', color='darkgreen', markersize=10, zorder=5, label='Receiver')
    ax.set_xlabel('Distance (km)')
    ax.set_ylabel('Elevation ASL (m)')
    ax.set_title('Terrain Profile & Fresnel Zone Clearance')
    ax.legend(loc='upper right')
    ax.grid(True, linestyle=':', alpha=0.6)
    y_min = min(ground.min(), lower_fresnel.min()) - 20
    y_max = max(ground.max(), upper_fresnel.max(), tx_asl, rx_asl) + 30
    ax.set_ylim(y_min, y_max)

    # Save to BytesIO
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf

# ----------------------------------------------------------------------
# PDF report builder
# ----------------------------------------------------------------------

def build_pdf_report(
    tower,
    receiver,
    link_result,
    terrain_profile: List[float],
    frequency_mhz: float
) -> io.BytesIO:
    """
    Build a complete PDF report and return as BytesIO buffer.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=20*mm, leftMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], alignment=TA_CENTER, fontSize=16)
    heading_style = ParagraphStyle('HeadingStyle', parent=styles['Heading2'], fontSize=12, spaceAfter=6)
    normal_style = styles['Normal']

    story = []

    # Title
    story.append(Paragraph("Engineering Link Analysis Report", title_style))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", normal_style))
    story.append(Spacer(1, 8*mm))

    # Tower Information table
    tower_data = [
        ['Parameter', 'Value'],
        ['ID', tower.id],
        ['Location', f"{tower.lat:.5f}, {tower.lon:.5f}"],
        ['Height (AGL)', f"{tower.height_m:.1f} m"],
        ['Operator', tower.operator],
        ['Bands', ', '.join([b.value for b in tower.bands])],
        ['TX Power', f"{tower.power_dbm:.1f} dBm"]
    ]
    tower_table = Table(tower_data, colWidths=[70*mm, 80*mm])
    tower_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
    ]))
    story.append(Paragraph("Tower Information", heading_style))
    story.append(tower_table)
    story.append(Spacer(1, 6*mm))

    # Receiver Information table
    rx_data = [
        ['Parameter', 'Value'],
        ['Location', f"{receiver.lat:.5f}, {receiver.lon:.5f}"],
        ['Height (AGL)', f"{receiver.height_m:.1f} m"],
        ['Antenna Gain', f"{receiver.antenna_gain_dbi:.1f} dBi"]
    ]
    rx_table = Table(rx_data, colWidths=[70*mm, 80*mm])
    rx_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
    ]))
    story.append(Paragraph("Receiver Information", heading_style))
    story.append(rx_table)
    story.append(Spacer(1, 6*mm))

    # Link Budget calculations
    f_hz = frequency_mhz * 1e6
    d_km = link_result.distance_km
    tx_gain = 17.0   # typical sector antenna
    rx_gain = receiver.antenna_gain_dbi
    fspl = _free_space_path_loss(d_km, f_hz)
    rx_sensitivity = -95.0  # standard 4G/5G threshold
    link_margin = link_result.signal_dbm - rx_sensitivity

    budget_data = [
        ['Parameter', 'Value', 'Unit'],
        ['TX Power', f"{tower.power_dbm:.1f}", 'dBm'],
        ['TX Antenna Gain', f"{tx_gain:.1f}", 'dBi'],
        ['RX Antenna Gain', f"{rx_gain:.1f}", 'dBi'],
        ['Frequency', f"{frequency_mhz:.0f}", 'MHz'],
        ['Distance', f"{d_km:.2f}", 'km'],
        ['Free Space Path Loss', f"{fspl:.1f}", 'dB'],
        ['Received Signal (RSSI)', f"{link_result.signal_dbm:.1f}", 'dBm'],
        ['Fresnel Clearance', f"{link_result.fresnel_clearance:.3f}", 'ratio'],
        ['RX Sensitivity Threshold', f"{rx_sensitivity:.1f}", 'dBm'],
        ['Link Margin', f"{link_margin:.1f}", 'dB']
    ]
    budget_table = Table(budget_data, colWidths=[70*mm, 50*mm, 30*mm])
    budget_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
    ]))
    story.append(Paragraph("Link Budget", heading_style))
    story.append(budget_table)
    story.append(PageBreak())

    # Link Analysis Results table
    results_data = [
        ['Metric', 'Value'],
        ['Feasible', 'YES' if link_result.feasible else 'NO'],
        ['Distance', f"{link_result.distance_km:.2f} km"],
        ['Signal (RSSI)', f"{link_result.signal_dbm:.1f} dBm"],
        ['Fresnel Clearance', f"{link_result.fresnel_clearance:.3f}"],
        ['Line of Sight', 'Clear' if link_result.los_ok else 'Obstructed']
    ]
    results_table = Table(results_data, colWidths=[70*mm, 80*mm])
    results_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
    ]))
    story.append(Paragraph("Link Analysis Results", heading_style))
    story.append(results_table)
    story.append(Spacer(1, 6*mm))

    # Recommendation
    story.append(Paragraph("Recommendation", heading_style))
    story.append(Paragraph(link_result.recommendation, normal_style))
    story.append(Spacer(1, 8*mm))

    # Terrain & Fresnel Zone plot (new page)
    story.append(PageBreak())
    story.append(Paragraph("Terrain & Fresnel Zone Analysis", heading_style))
    plot_buf = generate_fresnel_plot(terrain_profile, d_km, tower.height_m, receiver.height_m, f_hz)
    if plot_buf.getbuffer().nbytes == 0:
        story.append(Paragraph("Error: plot could not be generated", normal_style))
    else:
        img = Image(plot_buf, width=160*mm, height=80*mm)
        img.hAlign = 'CENTER'
        story.append(img)
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("<center>Figure: Line-of-sight and first Fresnel zone along path</center>", normal_style))

    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer
