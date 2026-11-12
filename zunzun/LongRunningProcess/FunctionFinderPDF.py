# PDF report for one page of function finder results: every equation listed on the page,
# ranked best first. Layout follows tkinterFit's FunctionFinderPDF.py
# (https://github.com/rossSauce/Tkinterfit), using the graphs the results page already drew.

import html
import os
import re
import time

import numpy
import pyeq3
import reportlab
import reportlab.lib.colors
import reportlab.lib.enums
import reportlab.lib.pagesizes
import reportlab.lib.styles
import reportlab.platypus
from reportlab.lib.units import inch

from .StatusMonitoredLongRunningProcessPage import NumberedCanvas

maxDataRows = 1000 # a huge data set would otherwise add hundreds of pages


def FormatNumber(value, formatString='%.6E'):
    if value is None:
        return 'n/a'
    try:
        if not numpy.isfinite(value):
            return 'n/a'
        return formatString % value
    except Exception:
        return 'n/a'


def EquationHTMLForPDF(equationHTML):
    # reportlab paragraphs understand <sup>, <sub> and <br/>; escape everything else
    text = html.escape(equationHTML, quote=False)
    text = re.sub(r'&lt;(/?)(sup|sub)&gt;', r'<\1\2>', text, flags=re.IGNORECASE)
    text = re.sub(r'&lt;br\s*/?&gt;', '<br/>', text, flags=re.IGNORECASE)
    return text


def _Styles():
    styles = reportlab.lib.styles.getSampleStyleSheet()
    ParagraphStyle = reportlab.lib.styles.ParagraphStyle
    styles.add(ParagraphStyle(name='CenteredBodyText', parent=styles['BodyText'], alignment=reportlab.lib.enums.TA_CENTER))
    styles.add(ParagraphStyle(name='Cell', parent=styles['BodyText'], fontSize=7, leading=8.5))
    styles.add(ParagraphStyle(name='HeaderCell', parent=styles['BodyText'], fontSize=7, leading=8.5, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='EquationHeading', parent=styles['Heading3'], spaceBefore=6, spaceAfter=2))
    styles.add(ParagraphStyle(name='Formula', parent=styles['BodyText'], fontSize=9, leading=13, leftIndent=12, spaceAfter=4))
    styles.add(ParagraphStyle(name='SmallText', parent=styles['BodyText'], fontSize=7, leading=8.5))
    return styles


def _Table(rows, styles, colWidths=None, header=True):
    cellRows = []
    for rowIndex, row in enumerate(rows):
        style = styles['HeaderCell'] if (header and rowIndex == 0) else styles['Cell']
        cellRows.append([reportlab.platypus.Paragraph(html.escape(str(cell), quote=False), style) for cell in row])
    table = reportlab.platypus.Table(cellRows, colWidths=colWidths, repeatRows=1 if header else 0, hAlign='LEFT')
    tableStyle = [('GRID', (0, 0), (-1, -1), 0.25, reportlab.lib.colors.grey),
                  ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                  ('TOPPADDING', (0, 0), (-1, -1), 1.5),
                  ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5)]
    if header:
        tableStyle.append(('BACKGROUND', (0, 0), (-1, 0), reportlab.lib.colors.HexColor('#E4E8EE')))
    table.setStyle(reportlab.platypus.TableStyle(tableStyle))
    return table


def _GraphRow(graphReports):
    # the page's small graphs (model, contour, error), side by side
    images = []
    for graph in graphReports:
        if os.path.isfile(graph.physicalFileLocation):
            image = reportlab.platypus.Image(graph.physicalFileLocation)
            image._restrictSize(2.35 * inch, 2.1 * inch)
            images.append(image)
    if not images:
        return None
    table = reportlab.platypus.Table([images], hAlign='CENTER')
    table.setStyle(reportlab.platypus.TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    return table


def _EquationDetails(entry, styles):
    rank = entry['rank']
    equation = entry['equation']
    elements = []
    elements.append(reportlab.platypus.Paragraph(
        'Rank %d: %s' % (rank, html.escape(equation.GetDisplayName())), styles['EquationHeading']))
    elements.append(reportlab.platypus.Paragraph(
        '%dD %s &nbsp;&nbsp;&nbsp; Fitting target (%s): %s' % (
            equation.GetDimensionality(), html.escape(entry['family']),
            equation.fittingTarget, FormatNumber(entry['targetValue'], '%.10E')),
        styles['BodyText']))
    elements.append(reportlab.platypus.Paragraph(EquationHTMLForPDF(equation.GetDisplayHTML()), styles['Formula']))

    rows = [['Coefficient', 'Value', 'Std Error', 't-stat', 'p-value', '95% Confidence Interval']]
    designators = equation.GetCoefficientDesignators()
    sd_beta = getattr(equation, 'sd_beta', None)
    tstat_beta = getattr(equation, 'tstat_beta', None)
    pstat_beta = getattr(equation, 'pstat_beta', None)
    ci = getattr(equation, 'ci', None)
    for i in range(len(equation.solvedCoefficients)):
        sd = None if sd_beta is None else sd_beta[i]
        t = None if tstat_beta is None else tstat_beta[i]
        p = None if pstat_beta is None else pstat_beta[i]
        if ci is None:
            interval = 'n/a'
        else:
            interval = '[%s, %s]' % (FormatNumber(ci[i][0], '%.5E'), FormatNumber(ci[i][1], '%.5E'))
        name = designators[i] if i < len(designators) else str(i)
        rows.append([name, FormatNumber(equation.solvedCoefficients[i], '%.16E'),
                     FormatNumber(sd, '%.5E'), FormatNumber(t, '%.5E'), FormatNumber(p, '%.5E'), interval])
    elements.append(_Table(rows, styles, colWidths=[0.75*inch, 1.6*inch, 0.85*inch, 0.85*inch, 0.85*inch, 2.1*inch]))
    elements.append(reportlab.platypus.Spacer(1, 4))

    def stat(name):
        return FormatNumber(getattr(equation, name, None))
    stats = [['RMSE', stat('rmse'), 'R-squared', stat('r2')],
             ['R-squared adjusted', stat('r2adj'), 'Model F-statistic', stat('Fstat')],
             ['F-statistic p-value', stat('Fpv'), 'Log-likelihood', stat('ll')],
             ['AIC', stat('aic'), 'BIC', stat('bic')],
             ['Degrees of freedom error', str(getattr(equation, 'df_e', 'n/a')),
              'Degrees of freedom regression', str(getattr(equation, 'df_r', 'n/a'))]]
    elements.append(_Table(stats, styles, colWidths=[1.5*inch, 2.0*inch, 1.6*inch, 2.0*inch], header=False))

    graphRow = _GraphRow(entry['graphs'])
    if graphRow is not None:
        elements.append(reportlab.platypus.Spacer(1, 4))
        elements.append(graphRow)
    elements.append(reportlab.platypus.Spacer(1, 14))
    # one equation never splits across pages
    return [reportlab.platypus.KeepTogether(elements)]


def CreatePDF(fileName, dimensionality, entries, totalEquationsFound, verseInfo):
    if not entries:
        raise ValueError('no equations to report')
    styles = _Styles()
    fittingTarget = entries[0]['equation'].fittingTarget
    targetName = fittingTarget + ' - ' + pyeq3.IModel.IModel.fittingTargetDictionary.get(fittingTarget, '')
    firstRank = entries[0]['rank']
    lastRank = entries[-1]['rank']

    elements = []
    elements.append(reportlab.platypus.Paragraph('ZunZunSite3 Function Finder Results', styles['Title']))
    elements.append(reportlab.platypus.Spacer(1, 12))
    summary = [['Dimensionality', '%dD' % dimensionality],
               ['Fitting target', targetName],
               ['Equations found', str(totalEquationsFound)],
               ['Ranks in this report', '%d to %d' % (firstRank, lastRank)],
               ['Created', time.asctime(time.localtime()) + ' local server time']]
    elements.append(_Table(summary, styles, colWidths=[2.0*inch, 5.0*inch], header=False))
    elements.append(reportlab.platypus.Spacer(1, 12))
    elements.append(reportlab.platypus.Paragraph(
        'Equations are ranked by their fitting target value, lowest (best) first. '
        'Each results page of the web site covers up to 40 equations; use the '
        '"Next Set Of Equations" link there for a PDF of the next ranks. '
        'Source code for any equation is on its own fitting results page.', styles['BodyText']))

    # the data that was fitted
    data = entries[0]['equation'].dataCache.allDataCacheDictionary
    if dimensionality == 2:
        rows = [['X', 'Y']]
        columns = [data['IndependentData'][0], data['DependentData']]
    else:
        rows = [['X', 'Y', 'Z']]
        columns = [data['IndependentData'][0], data['IndependentData'][1], data['DependentData']]
    count = 0
    for values in zip(*columns):
        rows.append(['%.10G' % v for v in values])
        count += 1
        if count >= maxDataRows:
            break
    elements.append(reportlab.platypus.Spacer(1, 12))
    elements.append(reportlab.platypus.Paragraph('Data', styles['Heading2']))
    if len(columns[0]) > maxDataRows:
        elements.append(reportlab.platypus.Paragraph('The first %d of %d data points:' % (maxDataRows, len(columns[0])), styles['BodyText']))
    elements.append(_Table(rows, styles, colWidths=[1.3*inch] * len(rows[0])))

    # inspirational message page, as in the site's other PDF reports
    elements.append(reportlab.platypus.PageBreak())
    elements.append(reportlab.platypus.Paragraph(verseInfo[0], styles['CenteredBodyText']))
    elements.append(reportlab.platypus.Spacer(1, 12))
    elements.append(reportlab.platypus.Paragraph(verseInfo[1], styles['CenteredBodyText']))
    elements.append(reportlab.platypus.PageBreak())

    # ranked summary
    elements.append(reportlab.platypus.Paragraph('Ranked Results', styles['Heading2']))
    rows = [['Rank', 'Equation', 'Family', 'Coefs', 'Fitting Target (%s)' % fittingTarget, 'R-squared', 'RMSE']]
    for entry in entries:
        equation = entry['equation']
        rows.append([str(entry['rank']), equation.GetDisplayName(), entry['family'],
                     str(len(equation.solvedCoefficients)), FormatNumber(entry['targetValue']),
                     FormatNumber(getattr(equation, 'r2', None), '%.8f'), FormatNumber(getattr(equation, 'rmse', None), '%.5E')])
    elements.append(_Table(rows, styles, colWidths=[0.45*inch, 2.4*inch, 1.1*inch, 0.45*inch, 1.05*inch, 0.8*inch, 0.85*inch]))

    # details of each equation
    elements.append(reportlab.platypus.PageBreak())
    elements.append(reportlab.platypus.Paragraph('Equation Details', styles['Heading2']))
    for entry in entries:
        try:
            elements.extend(_EquationDetails(entry, styles))
        except Exception:
            # one equation's odd statistics must not lose the whole report
            elements.append(reportlab.platypus.Paragraph('Rank %d: details could not be shown.' % entry['rank'], styles['BodyText']))

    # write to a temporary name first so a half-written file is never served
    temporaryName = fileName + '.partial'
    doc = reportlab.platypus.SimpleDocTemplate(temporaryName, pagesize=reportlab.lib.pagesizes.letter,
                                               leftMargin=0.6*inch, rightMargin=0.6*inch,
                                               topMargin=0.6*inch, bottomMargin=0.9*inch,
                                               title='ZunZunSite3 Function Finder Results')
    doc.build(elements, canvasmaker=NumberedCanvas)
    os.replace(temporaryName, fileName)
