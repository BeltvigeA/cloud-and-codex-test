"""Graphical dashboard client for managing printers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from datetime import datetime
from pathlib import Path
from typing import List

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import requests
from requests import RequestException

if __package__ in {None, ""}:
    currentFilePath = Path(__file__).resolve()
    projectRootPath = currentFilePath.parent.parent
    projectRootString = str(projectRootPath)
    if projectRootString not in sys.path:
        sys.path.append(projectRootString)
    from client.client import buildPendingUrl, defaultBaseUrl
else:
    from .client import buildPendingUrl, defaultBaseUrl


@dataclass
class PrinterInfo:
    printerName: str
    modelName: str
    ipAddress: str
    serialNumber: str
    status: str
    statusDetail: str
    statusColor: str


@dataclass
class JobInfo:
    jobNumber: str
    filename: str
    targetPrinter: str
    status: str
    material: str
    duration: str
    jobId: str | None = None
    uploadedAt: str | None = None


@dataclass
class ActivityEvent:
    level: str
    timestamp: str
    message: str
    category: str
    color: str


@dataclass
class KeyInfo:
    keyLabel: str
    keyValue: str


class NavigationList(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSpacing(6)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setFixedWidth(200)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self._contentHeight = 0

    def addDestination(self, label: str) -> None:
        item = QListWidgetItem(label)
        sizeHint = item.sizeHint()
        sizeHint.setHeight(sizeHint.height() + 8)
        item.setSizeHint(sizeHint)
        self.addItem(item)
        self.updateListHeight()

    def updateListHeight(self) -> None:
        totalHeight = self.frameWidth() * 2
        for index in range(self.count()):
            totalHeight += self.sizeHintForRow(index)
        totalHeight += max(0, self.count() - 1) * self.spacing()
        self._contentHeight = totalHeight
        self.setFixedHeight(totalHeight)


    def contentHeight(self) -> int:
        return self._contentHeight


class PrinterDashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PrintMaster Dashboard")
        self.resize(1280, 830)

        self.printers = self.samplePrinters()
        self.jobs: List[JobInfo] = []
        self.sampleJobsList: List[JobInfo] = []
        self.manualJobs: List[JobInfo] = []
        self.remoteJobsList: List[JobInfo] = []
        self.currentRemoteJobIds: set[str] = set()
        self.remoteJobsSignature: tuple[str, ...] | None = None
        self.events = self.sampleEvents()
        self.keys: List[KeyInfo] = []
        self.listenerChannel = "user-123"
        self.isListening = False
        self.jobCounter = (
            max((int(job.jobNumber.lstrip("#")) for job in self.jobs), default=0) + 1
        )
        self.backendBaseUrl = defaultBaseUrl
        self.listenerPollIntervalMs = 15000
        self.listenerPollTimer = QTimer(self)
        self.listenerPollTimer.setInterval(self.listenerPollIntervalMs)
        self.listenerPollTimer.timeout.connect(self.pollPendingJobs)
        self.listenerLastError: str | None = None

        self.metricValueLabels: dict[str, QLabel] = {}
        self.dashboardPrinterStatusLayout: QVBoxLayout | None = None
        self.dashboardActivityLayout: QVBoxLayout | None = None
        self.printerGridLayout: QGridLayout | None = None
        self.jobsLayout: QVBoxLayout | None = None
        self.jobsContainerLayout: QVBoxLayout | None = None
        self.keysLayout: QVBoxLayout | None = None
        self.eventsLayout: QVBoxLayout | None = None
        self.navigationWrapperLayout: QVBoxLayout | None = None
        self.navigationLogoWidget: QWidget | None = None
        self.rootLayout: QHBoxLayout | None = None
        self.navigationWrapperWidget: QWidget | None = None
        self.listenerStatusLabel: QLabel | None = None
        self.listenerStatusIndicator: QLabel | None = None
        self.listenerInput: QLineEdit | None = None
        self.listenerToggleButton: QPushButton | None = None

        self.mainWidget = QWidget()
        self.setCentralWidget(self.mainWidget)

        rootLayout = QHBoxLayout(self.mainWidget)
        rootLayout.setContentsMargins(24, 24, 24, 24)
        rootLayout.setSpacing(24)
        self.rootLayout = rootLayout

        self.navigationList = NavigationList()
        self.navigationList.addDestination("Dashboard")
        self.navigationList.addDestination("Printers")
        self.navigationList.addDestination("Job Queue")
        self.navigationList.addDestination("Listener")
        self.navigationList.addDestination("Keys")
        self.navigationList.addDestination("Events")

        self.navigationList.currentRowChanged.connect(self.changePage)

        navigationWrapper = QVBoxLayout()
        navigationWrapper.setContentsMargins(0, 0, 0, 0)
        navigationWrapper.setSpacing(16)
        self.navigationWrapperLayout = navigationWrapper

        logoWrapper = self.createLogoHeader()
        self.navigationLogoWidget = logoWrapper
        navigationWrapper.addWidget(logoWrapper)

        navigationScrollContent = QWidget()
        navigationScrollLayout = QVBoxLayout(navigationScrollContent)
        navigationScrollLayout.setContentsMargins(0, 0, 0, 0)
        navigationScrollLayout.setSpacing(0)
        navigationScrollLayout.addWidget(self.navigationList)
        navigationScrollLayout.addStretch(1)

        navigationScrollArea = QScrollArea()
        navigationScrollArea.setWidgetResizable(True)
        navigationScrollArea.setFrameShape(QFrame.NoFrame)
        navigationScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        navigationScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        navigationScrollArea.setWidget(navigationScrollContent)

        navigationWrapper.addWidget(navigationScrollArea, 1)
        navigationWrapperWidget = QWidget()
        navigationWrapperWidget.setLayout(navigationWrapper)
        navigationWrapperWidget.setFixedWidth(220)
        navigationWrapperWidget.setObjectName("navigationPanel")
        self.navigationWrapperWidget = navigationWrapperWidget

        self.pageStack = QStackedWidget()

        self.dashboardPage = self.createDashboardPage()
        self.printersPage = self.createPrintersPage()
        self.jobQueuePage = self.createJobQueuePage()
        self.listenerPage = self.createListenerPage()
        self.keysPage = self.createKeysPage()
        self.eventsPage = self.createEventsPage()

        for page in [
            self.dashboardPage,
            self.printersPage,
            self.jobQueuePage,
            self.listenerPage,
            self.keysPage,
            self.eventsPage,
        ]:
            self.pageStack.addWidget(page)

        rootLayout.addWidget(navigationWrapperWidget)
        rootLayout.addWidget(self.pageStack, 1)

        self.applyTheme()
        self.refreshDashboard()
        self.refreshPrintersGrid()
        self.refreshJobsTable()
        self.refreshKeysList()
        self.refreshEventsList()
        self.updateListenerStatus()
        self.navigationList.setCurrentRow(0)
        self.pollPendingJobs(force=True)
        self.ensureNavigationFits()


    def applyTheme(self) -> None:
        baseColor = "#101827"
        cardColor = "#111C33"
        accentColor = "#3B82F6"
        successColor = "#34D399"
        warningColor = "#F59E0B"
        errorColor = "#F87171"

        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {baseColor};
                color: #E5EDFF;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: 14px;
            }}
            QLabel#logoTitle {{
                font-size: 20px;
                font-weight: 600;
            }}
            QLabel#logoSubtitle {{
                color: #7B8AA5;
                font-size: 13px;
            }}
            QListWidget {{
                background-color: transparent;
            }}
            QListWidget::item {{
                padding: 10px 14px;
                border-radius: 8px;
                color: #93A5CE;
            }}
            QListWidget::item:selected {{
                background-color: {accentColor};
                color: white;
            }}
            QListWidget::item:hover {{
                background-color: rgba(59, 130, 246, 0.25);
            }}
            QWidget#navigationPanel {{
                background-color: {cardColor};
                border-radius: 16px;
                padding: 24px 12px;
            }}
            QFrame.card {{
                background-color: {cardColor};
                border-radius: 18px;
                padding: 22px;
            }}
            QLabel.sectionTitle {{
                font-size: 18px;
                font-weight: 600;
                margin-bottom: 16px;
            }}
            QLabel.metricTitle {{
                color: #7B8AA5;
                font-size: 14px;
            }}
            QLabel.metricValue {{
                font-size: 32px;
                font-weight: 700;
            }}
            QLabel.statusBadge {{
                font-size: 12px;
                font-weight: 600;
                padding: 4px 8px;
                border-radius: 12px;
            }}
            QLabel.statusSuccess {{
                background-color: rgba(52, 211, 153, 0.15);
                color: {successColor};
            }}
            QLabel.statusWarning {{
                background-color: rgba(245, 158, 11, 0.15);
                color: {warningColor};
            }}
            QLabel.statusError {{
                background-color: rgba(248, 113, 113, 0.15);
                color: {errorColor};
            }}
            QPushButton.primaryButton {{
                background-color: {accentColor};
                color: white;
                border-radius: 12px;
                padding: 10px 18px;
                font-weight: 600;
            }}
            QPushButton.primaryButton:hover {{
                background-color: #2563EB;
            }}
        """
        )

    def createLogoHeader(self) -> QWidget:
        logoLayout = QVBoxLayout()
        logoLayout.setContentsMargins(12, 0, 12, 0)
        logoLayout.setSpacing(6)

        titleLabel = QLabel("PrintMaster")
        titleLabel.setObjectName("logoTitle")
        subtitleLabel = QLabel("3D Print Manager")
        subtitleLabel.setObjectName("logoSubtitle")

        logoLayout.addWidget(titleLabel)
        logoLayout.addWidget(subtitleLabel)

        logoWidget = QWidget()
        logoWidget.setLayout(logoLayout)
        return logoWidget

    def ensureNavigationFits(self) -> None:
        if (
            not hasattr(self, "navigationWrapperLayout")
            or self.navigationWrapperLayout is None
            or self.rootLayout is None
        ):
            return

        navigationWidget = getattr(self, "navigationWrapperWidget", None)
        if navigationWidget is not None:
            navigationHeight = navigationWidget.sizeHint().height()
        else:
            navHeight = self.navigationList.contentHeight()
            margins = self.navigationWrapperLayout.getContentsMargins()
            _, layoutTop, _, layoutBottom = margins
            spacing = self.navigationWrapperLayout.spacing()
            headerHeight = (
                self.navigationLogoWidget.sizeHint().height()
                if self.navigationLogoWidget is not None
                else 0
            )
            navigationHeight = layoutTop + headerHeight + spacing + navHeight + layoutBottom

        rootMargins = self.rootLayout.contentsMargins()
        totalHeight = navigationHeight + rootMargins.top() + rootMargins.bottom()

        if totalHeight > self.minimumHeight():
            self.setMinimumHeight(totalHeight)
        if self.height() < totalHeight:
            self.resize(self.width(), totalHeight)

    def createDashboardPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        metricCard = QFrame()
        metricCard.setObjectName("metricsCard")
        metricCard.setProperty("class", "card")
        metricLayout = QHBoxLayout(metricCard)
        metricLayout.setSpacing(32)

        self.metricValueLabels.clear()
        for title in [
            "Total Printers",
            "Active Jobs",
            "Queued Jobs",
            "Online Printers",
        ]:
            card = self.createMetricWidget(title)
            metricLayout.addWidget(card)

        printerStatusCard = QFrame()
        printerStatusCard.setProperty("class", "card")
        printerStatusLayout = QVBoxLayout(printerStatusCard)
        printerStatusLayout.setSpacing(16)

        printerStatusTitle = QLabel("Printer Status")
        printerStatusTitle.setProperty("class", "sectionTitle")
        printerStatusLayout.addWidget(printerStatusTitle)

        self.dashboardPrinterStatusLayout = QVBoxLayout()
        self.dashboardPrinterStatusLayout.setSpacing(12)
        printerStatusLayout.addLayout(self.dashboardPrinterStatusLayout)
        printerStatusLayout.addStretch(1)

        recentActivityCard = QFrame()
        recentActivityCard.setProperty("class", "card")
        recentActivityLayout = QVBoxLayout(recentActivityCard)
        recentActivityLayout.setSpacing(16)

        recentActivityTitle = QLabel("Recent Activity")
        recentActivityTitle.setProperty("class", "sectionTitle")
        recentActivityLayout.addWidget(recentActivityTitle)

        self.dashboardActivityLayout = QVBoxLayout()
        self.dashboardActivityLayout.setSpacing(12)
        recentActivityLayout.addLayout(self.dashboardActivityLayout)
        recentActivityLayout.addStretch(1)

        lowerLayout = QHBoxLayout()
        lowerLayout.setSpacing(20)
        lowerLayout.addWidget(printerStatusCard, 2)
        lowerLayout.addWidget(recentActivityCard, 1)

        layout.addWidget(metricCard)
        layout.addLayout(lowerLayout)

        return page

    def createPrintersPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        headerRow = QHBoxLayout()
        title = QLabel("3D Printers")
        title.setProperty("class", "sectionTitle")
        addButton = QPushButton("Add Printer")
        addButton.setObjectName("addPrinterButton")
        addButton.setProperty("class", "primaryButton")
        addButton.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        addButton.clicked.connect(self.showAddPrinterDialog)
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addButton)

        layout.addLayout(headerRow)

        self.printerGridLayout = QGridLayout()
        self.printerGridLayout.setSpacing(20)
        layout.addLayout(self.printerGridLayout)
        layout.addStretch(1)

        return page

    def createJobQueuePage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        headerRow = QHBoxLayout()
        title = QLabel("Print Queue")
        title.setProperty("class", "sectionTitle")
        addButton = QPushButton("Add Job")
        addButton.setProperty("class", "primaryButton")
        addButton.clicked.connect(self.showAddJobDialog)
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addButton)

        layout.addLayout(headerRow)

        jobsCard = QFrame()
        jobsCard.setProperty("class", "card")
        self.jobsLayout = QVBoxLayout(jobsCard)
        self.jobsLayout.setSpacing(12)

        headerLabels = [
            "#",
            "Filename",
            "Target Printer",
            "Status",
            "Material",
            "Duration",
            "Actions",
        ]
        headerRowWidget = self.createTableRow(headerLabels, header=True)
        self.jobsLayout.addWidget(headerRowWidget)

        self.jobsContainerLayout = QVBoxLayout()
        self.jobsContainerLayout.setSpacing(8)
        self.jobsLayout.addLayout(self.jobsContainerLayout)

        layout.addWidget(jobsCard)
        layout.addStretch(1)

        return page

    def createListenerPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        title = QLabel("Listener Settings")
        title.setProperty("class", "sectionTitle")
        layout.addWidget(title)

        listenerCard = QFrame()
        listenerCard.setProperty("class", "card")
        cardLayout = QVBoxLayout(listenerCard)
        cardLayout.setSpacing(16)

        statusRow = QHBoxLayout()
        statusRow.setSpacing(8)
        self.listenerStatusIndicator = QLabel("●")
        self.listenerStatusIndicator.setFont(QFont("Segoe UI", 18, QFont.Bold))
        self.listenerStatusLabel = QLabel("")
        statusRow.addWidget(self.listenerStatusIndicator)
        statusRow.addWidget(self.listenerStatusLabel)
        statusRow.addStretch(1)
        cardLayout.addLayout(statusRow)

        formLayout = QFormLayout()
        formLayout.setLabelAlignment(Qt.AlignLeft)
        self.listenerInput = QLineEdit(self.listenerChannel)
        formLayout.addRow("Recipient / Channel", self.listenerInput)
        cardLayout.addLayout(formLayout)

        buttonRow = QHBoxLayout()
        buttonRow.setSpacing(12)
        saveButton = QPushButton("Save Channel")
        saveButton.setProperty("class", "primaryButton")
        saveButton.clicked.connect(self.saveListenerChannel)
        self.listenerToggleButton = QPushButton("Start Listening")
        self.listenerToggleButton.setProperty("class", "primaryButton")
        self.listenerToggleButton.clicked.connect(self.toggleListening)
        buttonRow.addWidget(saveButton)
        buttonRow.addWidget(self.listenerToggleButton)
        buttonRow.addStretch(1)
        cardLayout.addLayout(buttonRow)

        layout.addWidget(listenerCard)
        layout.addStretch(1)

        return page

    def createKeysPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        headerRow = QHBoxLayout()
        title = QLabel("Public Keys")
        title.setProperty("class", "sectionTitle")
        addButton = QPushButton("Add Key")
        addButton.setProperty("class", "primaryButton")
        addButton.clicked.connect(self.showAddKeyDialog)
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addButton)

        layout.addLayout(headerRow)

        keysCard = QFrame()
        keysCard.setProperty("class", "card")
        self.keysLayout = QVBoxLayout(keysCard)
        self.keysLayout.setSpacing(12)

        layout.addWidget(keysCard)
        layout.addStretch(1)

        return page

    def createEventsPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        headerRow = QHBoxLayout()
        title = QLabel("System Events")
        title.setProperty("class", "sectionTitle")
        addEventButton = QPushButton("Add Event")
        addEventButton.setProperty("class", "primaryButton")
        addEventButton.clicked.connect(self.showAddEventDialog)
        clearButton = QPushButton("Clear Events")
        clearButton.setProperty("class", "primaryButton")
        clearButton.clicked.connect(self.clearEvents)
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addEventButton)
        headerRow.addWidget(clearButton)

        layout.addLayout(headerRow)

        eventsCard = QFrame()
        eventsCard.setProperty("class", "card")
        cardLayout = QVBoxLayout(eventsCard)
        cardLayout.setSpacing(12)

        logTitle = QLabel("Event Log")
        logTitle.setProperty("class", "sectionTitle")
        cardLayout.addWidget(logTitle)

        self.eventsLayout = QVBoxLayout()
        self.eventsLayout.setSpacing(8)
        cardLayout.addLayout(self.eventsLayout)

        layout.addWidget(eventsCard)
        layout.addStretch(1)

        return page

    def changePage(self, index: int) -> None:
        self.pageStack.setCurrentIndex(index)

    def createMetricWidget(self, title: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)

        titleLabel = QLabel(title)
        titleLabel.setProperty("class", "metricTitle")
        valueLabel = QLabel("0")
        valueLabel.setProperty("class", "metricValue")

        self.metricValueLabels[title] = valueLabel

        layout.addWidget(titleLabel)
        layout.addWidget(valueLabel)

        return widget

    def createPrinterStatusRow(self, printer: PrinterInfo) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(12)

        nameLabel = QLabel(printer.printerName)
        nameLabel.setFont(QFont("Segoe UI", 14, QFont.Bold))
        headerLayout.addWidget(nameLabel)

        statusLabel = QLabel(printer.status.capitalize())
        badgeClass = {
            "success": "statusSuccess",
            "warning": "statusWarning",
            "error": "statusError",
        }.get(printer.statusColor, "statusSuccess")
        statusLabel.setProperty("class", f"statusBadge {badgeClass}")
        headerLayout.addWidget(statusLabel)
        headerLayout.addStretch(1)

        layout.addLayout(headerLayout)

        detailLabel = QLabel(printer.statusDetail)
        detailLabel.setStyleSheet("color: #7B8AA5;")
        layout.addWidget(detailLabel)

        return row

    def createActivityRow(self, event: ActivityEvent) -> QWidget:
        row = QWidget()
        rowLayout = QHBoxLayout(row)
        rowLayout.setSpacing(12)
        rowLayout.setContentsMargins(0, 0, 0, 0)

        badge = QLabel(event.level)
        badge.setProperty("class", "statusBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"background-color: rgba({event.color}, 0.18); color: rgb({event.color});"
        )
        badge.setFixedWidth(80)

        messageLayout = QVBoxLayout()
        messageLayout.setSpacing(2)

        messageLabel = QLabel(event.message)
        messageLabel.setWordWrap(True)
        metaLabel = QLabel(f"{event.timestamp} • {event.category}")
        metaLabel.setStyleSheet("color: #7B8AA5;")

        messageLayout.addWidget(messageLabel)
        messageLayout.addWidget(metaLabel)

        rowLayout.addWidget(badge)
        rowLayout.addLayout(messageLayout, 1)

        return row

    def createPrinterCard(self, printer: PrinterInfo) -> QWidget:
        card = QFrame()
        card.setProperty("class", "card")
        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        headerRow = QHBoxLayout()
        headerRow.setSpacing(8)
        nameLabel = QLabel(printer.printerName)
        nameLabel.setFont(QFont("Segoe UI", 16, QFont.Bold))
        statusBadge = QLabel(printer.status.capitalize())
        badgeClass = {
            "success": "statusSuccess",
            "warning": "statusWarning",
            "error": "statusError",
        }.get(printer.statusColor, "statusSuccess")
        statusBadge.setProperty("class", f"statusBadge {badgeClass}")
        headerRow.addWidget(nameLabel)
        headerRow.addWidget(statusBadge)
        headerRow.addStretch(1)

        layout.addLayout(headerRow)

        layout.addWidget(QLabel(f"Model\n{printer.modelName}"))
        layout.addWidget(QLabel(f"IP Address\n{printer.ipAddress}"))
        layout.addWidget(QLabel(f"Serial\n{printer.serialNumber}"))
        layout.addWidget(QLabel(f"Current Job\n{printer.statusDetail}"))

        buttonRow = QHBoxLayout()
        buttonRow.setSpacing(8)
        removeButton = QPushButton("Remove")
        removeButton.clicked.connect(partial(self.removePrinter, printer))
        buttonRow.addWidget(removeButton)
        buttonRow.addStretch(1)
        layout.addLayout(buttonRow)

        return card

    def createTableRow(self, values: List[str], header: bool = False) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setSpacing(12)
        layout.setContentsMargins(4, 4, 4, 4)

        for value in values:
            label = QLabel(value)
            label.setStyleSheet(
                "font-weight: 600; color: #E5EDFF;"
                if header
                else "color: #CBD5F5;"
            )
            label.setMinimumWidth(100)
            layout.addWidget(label)

        layout.addStretch(1)
        return row

    def createEventLogRow(self, event: ActivityEvent) -> QWidget:
        row = QFrame()
        row.setFrameShape(QFrame.StyledPanel)
        row.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.04); border-radius: 12px; padding: 12px;"
        )
        layout = QHBoxLayout(row)
        layout.setSpacing(12)

        statusBadge = QLabel(event.level)
        statusBadge.setProperty("class", "statusBadge")
        statusBadge.setStyleSheet(
            f"background-color: rgba({event.color}, 0.18); color: rgb({event.color});"
        )
        statusBadge.setFixedWidth(80)
        statusBadge.setAlignment(Qt.AlignCenter)

        messageLayout = QVBoxLayout()
        messageLayout.setSpacing(4)

        messageLabel = QLabel(event.message)
        messageLabel.setStyleSheet("color: #E5EDFF;")
        detailsLabel = QLabel(f"{event.timestamp} • {event.category}")
        detailsLabel.setStyleSheet("color: #7B8AA5;")

        messageLayout.addWidget(messageLabel)
        messageLayout.addWidget(detailsLabel)

        layout.addWidget(statusBadge)
        layout.addLayout(messageLayout, 1)
        removeButton = QPushButton("Remove")
        removeButton.clicked.connect(partial(self.removeEvent, event))
        layout.addWidget(removeButton)

        return row

    def clearLayout(self, layout: QLayout | None) -> None:
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
            else:
                childLayout = item.layout()
                if childLayout is not None:
                    self.clearLayout(childLayout)

    def refreshDashboard(self) -> None:
        self.updateMetricValues()
        self.populateDashboardPrinters()
        self.populateDashboardActivity()

    def updateMetricValues(self) -> None:
        activeJobs = sum(
            1 for job in self.jobs if job.status.lower() in {"printing", "in progress"}
        )
        queuedJobs = sum(1 for job in self.jobs if job.status.lower() == "queued")
        onlinePrinters = sum(1 for printer in self.printers if printer.statusColor == "success")
        metrics = {
            "Total Printers": len(self.printers),
            "Active Jobs": activeJobs,
            "Queued Jobs": queuedJobs,
            "Online Printers": onlinePrinters,
        }
        for title, value in metrics.items():
            label = self.metricValueLabels.get(title)
            if label is not None:
                label.setText(str(value))

    def populateDashboardPrinters(self) -> None:
        if self.dashboardPrinterStatusLayout is None:
            return
        self.clearLayout(self.dashboardPrinterStatusLayout)
        if not self.printers:
            emptyLabel = QLabel("No printers registered yet")
            emptyLabel.setStyleSheet("color: #7B8AA5;")
            self.dashboardPrinterStatusLayout.addWidget(emptyLabel)
            return
        for printer in self.printers:
            statusRow = self.createPrinterStatusRow(printer)
            self.dashboardPrinterStatusLayout.addWidget(statusRow)

    def populateDashboardActivity(self) -> None:
        if self.dashboardActivityLayout is None:
            return
        self.clearLayout(self.dashboardActivityLayout)
        if not self.events:
            emptyLabel = QLabel("No recent activity yet")
            emptyLabel.setStyleSheet("color: #7B8AA5;")
            self.dashboardActivityLayout.addWidget(emptyLabel)
            return
        for event in reversed(self.events[-4:]):
            eventWidget = self.createActivityRow(event)
            self.dashboardActivityLayout.addWidget(eventWidget)

    def refreshPrintersGrid(self) -> None:
        if self.printerGridLayout is None:
            return
        self.clearLayout(self.printerGridLayout)
        if not self.printers:
            placeholder = QLabel("Add your first printer to get started")
            placeholder.setStyleSheet("color: #7B8AA5;")
            self.printerGridLayout.addWidget(placeholder, 0, 0)
            return
        for index, printer in enumerate(self.printers):
            card = self.createPrinterCard(printer)
            row = index // 2
            column = index % 2
            self.printerGridLayout.addWidget(card, row, column)

    def showAddPrinterDialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Printer")
        formLayout = QFormLayout(dialog)

        nameInput = QLineEdit()
        modelInput = QLineEdit()
        ipInput = QLineEdit()
        serialInput = QLineEdit()
        detailInput = QLineEdit("Ready for next job")
        statusCombo = QComboBox()
        statusCombo.addItems(["Printing", "Idle", "Error"])

        formLayout.addRow("Printer Name", nameInput)
        formLayout.addRow("Model", modelInput)
        formLayout.addRow("IP Address", ipInput)
        formLayout.addRow("Serial Number", serialInput)
        formLayout.addRow("Current Job", detailInput)
        formLayout.addRow("Status", statusCombo)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        formLayout.addWidget(buttonBox)

        def submit() -> None:
            printerName = nameInput.text().strip()
            modelName = modelInput.text().strip() or "Unknown"
            ipAddress = ipInput.text().strip()
            serialNumber = serialInput.text().strip()
            statusDetail = detailInput.text().strip() or "Ready for next job"
            if not printerName or not ipAddress or not serialNumber:
                QMessageBox.warning(
                    self,
                    "Missing information",
                    "Printer name, IP address, and serial number are required.",
                )
                return
            statusText = statusCombo.currentText()
            statusColor = {
                "Printing": "success",
                "Idle": "warning",
                "Error": "error",
            }.get(statusText, "success")
            printer = PrinterInfo(
                printerName=printerName,
                modelName=modelName,
                ipAddress=ipAddress,
                serialNumber=serialNumber,
                status=statusText.lower(),
                statusDetail=statusDetail,
                statusColor=statusColor,
            )
            self.printers.append(printer)
            self.refreshPrintersGrid()
            self.refreshDashboard()
            self.logEvent(
                ActivityEvent(
                    level="INFO",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Added printer '{printer.printerName}'",
                    category="printers",
                    color=self.getEventColor("INFO"),
                )
            )
            dialog.accept()

        buttonBox.accepted.connect(submit)
        buttonBox.rejected.connect(dialog.reject)
        dialog.exec()

    def removePrinter(self, printer: PrinterInfo) -> None:
        if printer in self.printers:
            self.printers.remove(printer)
            self.refreshPrintersGrid()
            self.refreshDashboard()
            self.logEvent(
                ActivityEvent(
                    level="WARNING",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Removed printer '{printer.printerName}'",
                    category="printers",
                    color=self.getEventColor("WARNING"),
                )
            )

    def showAddJobDialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Send Print Job")
        formLayout = QFormLayout(dialog)

        filenameInput = QLineEdit()
        targetCombo = QComboBox()
        targetCombo.setEditable(True)
        for printer in self.printers:
            targetCombo.addItem(
                f"{printer.printerName} ({printer.ipAddress})", printer.ipAddress
            )
        statusCombo = QComboBox()
        statusCombo.addItems(["Queued", "Printing", "Completed"])
        materialInput = QLineEdit("PLA")
        durationInput = QLineEdit("60m")

        formLayout.addRow("Filename", filenameInput)
        formLayout.addRow("Target Printer", targetCombo)
        formLayout.addRow("Status", statusCombo)
        formLayout.addRow("Material", materialInput)
        formLayout.addRow("Duration", durationInput)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        formLayout.addWidget(buttonBox)

        def submit() -> None:
            filename = filenameInput.text().strip()
            targetPrinter = targetCombo.currentData()
            if not targetPrinter:
                targetPrinter = targetCombo.currentText().strip()
            status = statusCombo.currentText()
            material = materialInput.text().strip() or "PLA"
            duration = durationInput.text().strip() or "60m"
            if not filename or not targetPrinter:
                QMessageBox.warning(
                    self,
                    "Missing information",
                    "Filename and target printer are required to create a job.",
                )
                return
            job = JobInfo(
                jobNumber=self.generateJobNumber(),
                filename=filename,
                targetPrinter=targetPrinter,
                status=status,
                material=material,
                duration=duration,
                jobId=self.generateManualJobId(),
            )
            self.addManualJob(job)
            self.logEvent(
                ActivityEvent(
                    level="SUCCESS",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Received new job '{job.filename}'",
                    category="jobs",
                    color=self.getEventColor("SUCCESS"),
                )
            )
            dialog.accept()

        buttonBox.accepted.connect(submit)
        buttonBox.rejected.connect(dialog.reject)
        dialog.exec()

    def generateJobNumber(self) -> str:
        jobNumber = f"#{self.jobCounter}"
        self.jobCounter += 1
        return jobNumber

    def generateManualJobId(self) -> str:
        return f"manual-{max(0, self.jobCounter - 1)}"

    def createJobRow(self, job: JobInfo) -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.04); border-radius: 12px; padding: 10px;"
        )
        layout = QHBoxLayout(row)
        layout.setSpacing(12)
        layout.setContentsMargins(8, 8, 8, 8)

        for value in [
            job.jobNumber,
            job.filename,
            job.targetPrinter,
            job.status,
            job.material,
            job.duration,
        ]:
            label = QLabel(value)
            label.setStyleSheet("color: #CBD5F5;")
            label.setMinimumWidth(100)
            layout.addWidget(label)

        layout.addStretch(1)
        detailsButton = QPushButton("Details")
        detailsButton.clicked.connect(partial(self.showJobDetails, job))
        layout.addWidget(detailsButton)
        removeButton = QPushButton("Remove")
        removeButton.clicked.connect(partial(self.removeJob, job))
        layout.addWidget(removeButton)

        return row

    def showJobDetails(self, job: JobInfo) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Job Details")
        dialogLayout = QVBoxLayout(dialog)
        dialogLayout.setSpacing(16)

        infoLayout = QFormLayout()
        infoLayout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        infoLayout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)

        def addInfoRow(label: str, value: str | None) -> None:
            valueLabel = QLabel(value or "-")
            valueLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
            infoLayout.addRow(label, valueLabel)

        addInfoRow("Job Number", job.jobNumber or "-")
        addInfoRow("Filename", job.filename)
        addInfoRow("Target Printer", job.targetPrinter)
        addInfoRow("Status", job.status)
        addInfoRow("Material", job.material)
        addInfoRow("Duration", job.duration)
        addInfoRow("Job ID", job.jobId or "-")
        addInfoRow("Uploaded", job.uploadedAt or "-")

        dialogLayout.addLayout(infoLayout)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Close)
        buttonBox.rejected.connect(dialog.reject)
        buttonBox.accepted.connect(dialog.accept)
        dialogLayout.addWidget(buttonBox)

        buttonBox.button(QDialogButtonBox.Close).setText("Close")
        dialog.exec()

    def refreshJobsTable(self) -> None:
        if self.jobsContainerLayout is None:
            return
        self.clearLayout(self.jobsContainerLayout)
        if not self.jobs:
            placeholder = QLabel("No jobs in the queue yet")
            placeholder.setStyleSheet("color: #7B8AA5;")
            self.jobsContainerLayout.addWidget(placeholder)
            return
        for job in reversed(self.jobs):
            self.jobsContainerLayout.addWidget(self.createJobRow(job))

    def addManualJob(self, job: JobInfo) -> None:
        self.manualJobs.insert(0, job)
        self.updateCombinedJobs()

    def updateCombinedJobs(self, remoteJobs: List[JobInfo] | None = None) -> None:
        if remoteJobs is not None:
            self.remoteJobsList = remoteJobs
        combinedJobs: List[JobInfo] = []
        combinedJobs.extend(self.remoteJobsList)
        combinedJobs.extend(self.manualJobs)
        self.jobs = combinedJobs
        self.refreshJobsTable()
        self.refreshDashboard()

    def removeJob(self, job: JobInfo) -> None:
        if job.jobId and job.jobId in self.currentRemoteJobIds:
            QMessageBox.information(
                self,
                "Remote job",
                "Cloud-managed jobs can only be cleared from the server dashboard.",
            )
            return
        if job in self.manualJobs:
            self.manualJobs.remove(job)
        elif job in self.jobs:
            self.jobs.remove(job)
        else:
            return
        self.updateCombinedJobs()
        self.logEvent(
            ActivityEvent(
                level="WARNING",
                timestamp=datetime.now().strftime("%H:%M"),
                message=f"Removed job '{job.filename}'",
                category="jobs",
                color=self.getEventColor("WARNING"),
            )
        )

    def formatJobDuration(self, rawDuration: object) -> str:
        if rawDuration in (None, ""):
            return "-"
        if isinstance(rawDuration, (int, float)):
            totalMinutes = int(rawDuration)
            if totalMinutes <= 0:
                return "-"
            hours, minutes = divmod(totalMinutes, 60)
            if hours:
                return f"{hours}h {minutes:02d}m"
            return f"{minutes}m"
        return str(rawDuration)

    def normalizeTimestamp(self, rawTimestamp: object) -> str | None:
        if isinstance(rawTimestamp, str):
            return rawTimestamp
        if isinstance(rawTimestamp, datetime):
            return rawTimestamp.strftime("%H:%M")
        return None

    def showAddKeyDialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Add API Key")
        formLayout = QFormLayout(dialog)

        labelInput = QLineEdit()
        valueInput = QLineEdit()

        formLayout.addRow("Key Label", labelInput)
        formLayout.addRow("Key Value", valueInput)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        formLayout.addWidget(buttonBox)

        def submit() -> None:
            keyLabel = labelInput.text().strip()
            keyValue = valueInput.text().strip()
            if not keyLabel or not keyValue:
                QMessageBox.warning(
                    self,
                    "Missing information",
                    "Both key label and key value are required.",
                )
                return
            key = KeyInfo(keyLabel=keyLabel, keyValue=keyValue)
            self.keys.append(key)
            self.refreshKeysList()
            self.logEvent(
                ActivityEvent(
                    level="INFO",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Added key '{key.keyLabel}'",
                    category="security",
                    color=self.getEventColor("INFO"),
                )
            )
            dialog.accept()

        buttonBox.accepted.connect(submit)
        buttonBox.rejected.connect(dialog.reject)
        dialog.exec()

    def createKeyRow(self, key: KeyInfo) -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.04); border-radius: 12px; padding: 12px;"
        )
        layout = QHBoxLayout(row)
        layout.setSpacing(12)

        label = QLabel(key.keyLabel)
        label.setStyleSheet("font-weight: 600; color: #E5EDFF;")
        valueLabel = QLabel(key.keyValue)
        valueLabel.setStyleSheet(
            "color: #CBD5F5; font-family: 'JetBrains Mono', 'Fira Code', monospace;"
        )

        layout.addWidget(label)
        layout.addWidget(valueLabel, 1)

        removeButton = QPushButton("Remove")
        removeButton.clicked.connect(partial(self.removeKey, key))
        layout.addWidget(removeButton)

        return row

    def refreshKeysList(self) -> None:
        if self.keysLayout is None:
            return
        self.clearLayout(self.keysLayout)
        if not self.keys:
            placeholder = QLabel("No public keys configured yet")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #7B8AA5; font-size: 16px;")
            self.keysLayout.addWidget(placeholder)
            return
        for key in self.keys:
            self.keysLayout.addWidget(self.createKeyRow(key))

    def removeKey(self, key: KeyInfo) -> None:
        if key in self.keys:
            self.keys.remove(key)
            self.refreshKeysList()
            self.logEvent(
                ActivityEvent(
                    level="WARNING",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Removed key '{key.keyLabel}'",
                    category="security",
                    color=self.getEventColor("WARNING"),
                )
            )

    def showAddEventDialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Log Event")
        formLayout = QFormLayout(dialog)

        levelCombo = QComboBox()
        levelCombo.addItems(["INFO", "SUCCESS", "WARNING", "ERROR"])
        messageInput = QLineEdit()
        categoryInput = QLineEdit("system")

        formLayout.addRow("Level", levelCombo)
        formLayout.addRow("Message", messageInput)
        formLayout.addRow("Category", categoryInput)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        formLayout.addWidget(buttonBox)

        def submit() -> None:
            level = levelCombo.currentText()
            message = messageInput.text().strip()
            category = categoryInput.text().strip() or "system"
            if not message:
                QMessageBox.warning(
                    self,
                    "Missing information",
                    "Event message cannot be empty.",
                )
                return
            event = ActivityEvent(
                level=level,
                timestamp=datetime.now().strftime("%H:%M"),
                message=message,
                category=category,
                color=self.getEventColor(level),
            )
            self.logEvent(event)
            dialog.accept()

        buttonBox.accepted.connect(submit)
        buttonBox.rejected.connect(dialog.reject)
        dialog.exec()

    def logEvent(self, event: ActivityEvent) -> None:
        self.events.append(event)
        self.refreshEventsList()
        self.populateDashboardActivity()

    def refreshEventsList(self) -> None:
        if self.eventsLayout is None:
            return
        self.clearLayout(self.eventsLayout)
        if not self.events:
            placeholder = QLabel("No events logged yet")
            placeholder.setStyleSheet("color: #7B8AA5;")
            self.eventsLayout.addWidget(placeholder)
            return
        for event in reversed(self.events):
            self.eventsLayout.addWidget(self.createEventLogRow(event))

    def removeEvent(self, event: ActivityEvent) -> None:
        if event in self.events:
            self.events.remove(event)
            self.refreshEventsList()
            self.populateDashboardActivity()

    def clearEvents(self) -> None:
        if not self.events:
            return
        self.events.clear()
        self.refreshEventsList()
        self.populateDashboardActivity()

    def toggleListening(self) -> None:
        self.isListening = not self.isListening
        statusLevel = "SUCCESS" if self.isListening else "INFO"
        statusMessage = (
            f"Started listening on channel '{self.listenerChannel}'"
            if self.isListening
            else "Stopped listening for jobs"
        )
        if self.isListening:
            self.listenerPollTimer.start()
            self.pollPendingJobs(force=True)
        else:
            self.listenerPollTimer.stop()
            self.listenerLastError = None
        self.updateListenerStatus()
        self.logEvent(
            ActivityEvent(
                level=statusLevel,
                timestamp=datetime.now().strftime("%H:%M"),
                message=statusMessage,
                category="listener",
                color=self.getEventColor(statusLevel),
            )
        )

    def pollPendingJobs(self, force: bool = False) -> None:
        if not force and not self.isListening:
            return
        try:
            pendingUrl = buildPendingUrl(self.backendBaseUrl, self.listenerChannel)
        except ValueError as error:
            self.listenerPollTimer.stop()
            self.isListening = False
            self.updateListenerStatus()
            errorMessage = f"Invalid backend address: {error}"
            if self.listenerLastError != errorMessage:
                self.listenerLastError = errorMessage
                self.logEvent(
                    ActivityEvent(
                        level="ERROR",
                        timestamp=datetime.now().strftime("%H:%M"),
                        message=errorMessage,
                        category="listener",
                        color=self.getEventColor("ERROR"),
                    )
                )
            QMessageBox.warning(self, "Invalid backend URL", errorMessage)
            return

        try:
            response = requests.get(pendingUrl, timeout=10)
            response.raise_for_status()
        except RequestException as error:
            warningMessage = f"Unable to reach cloud queue: {error}".rstrip(".")
            if self.listenerLastError != warningMessage:
                self.listenerLastError = warningMessage
                self.logEvent(
                    ActivityEvent(
                        level="WARNING",
                        timestamp=datetime.now().strftime("%H:%M"),
                        message=warningMessage,
                        category="listener",
                        color=self.getEventColor("WARNING"),
                    )
                )
            return

        try:
            payload = response.json()
        except ValueError:
            errorMessage = "Received invalid response from cloud queue"
            if self.listenerLastError != errorMessage:
                self.listenerLastError = errorMessage
                self.logEvent(
                    ActivityEvent(
                        level="ERROR",
                        timestamp=datetime.now().strftime("%H:%M"),
                        message=errorMessage,
                        category="listener",
                        color=self.getEventColor("ERROR"),
                    )
                )
            return

        self.listenerLastError = None

        pendingFiles = payload.get("pendingFiles")
        if not isinstance(pendingFiles, list):
            pendingFiles = []

        remoteJobs: List[JobInfo] = []
        for rawIndex, pending in enumerate(pendingFiles, start=1):
            if not isinstance(pending, dict):
                continue
            jobIdSource = pending.get("fileId") or pending.get("fetchToken") or rawIndex
            jobId = str(jobIdSource)
            filename = pending.get("originalFilename") or pending.get("fileId") or f"Job {rawIndex}"
            statusRaw = pending.get("status")
            statusText = statusRaw.strip() if isinstance(statusRaw, str) else "Pending"
            statusDisplay = statusText.replace("_", " ").title() if statusText else "Pending"
            materialValue = pending.get("material") or pending.get("materialType") or "-"
            targetPrinter = (
                pending.get("targetPrinter")
                or pending.get("printerSerial")
                or pending.get("recipientId")
                or "-"
            )
            durationValue = pending.get("duration") or pending.get("estimatedDuration")
            uploadedAt = self.normalizeTimestamp(pending.get("uploadedAt"))
            remoteJobs.append(
                JobInfo(
                    jobNumber="",
                    filename=str(filename),
                    targetPrinter=str(targetPrinter),
                    status=statusDisplay,
                    material=str(materialValue),
                    duration=self.formatJobDuration(durationValue),
                    jobId=jobId,
                    uploadedAt=uploadedAt,
                )
            )

        remoteJobs.sort(key=lambda job: job.uploadedAt or "", reverse=True)
        for index, job in enumerate(remoteJobs, start=1):
            job.jobNumber = f"#{index}"

        self.currentRemoteJobIds = {job.jobId for job in remoteJobs if job.jobId}

        if self.sampleJobsList:
            self.manualJobs = [job for job in self.manualJobs if job not in self.sampleJobsList]
            self.sampleJobsList = []

        newSignature = tuple(sorted(f"{job.jobId}:{job.status}" for job in remoteJobs if job.jobId))
        if self.remoteJobsSignature != newSignature:
            self.remoteJobsSignature = newSignature
            message = (
                f"Cloud queue updated - {len(remoteJobs)} pending job(s)"
                if remoteJobs
                else "Cloud queue updated - no pending jobs"
            )
            self.logEvent(
                ActivityEvent(
                    level="INFO",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=message,
                    category="jobs",
                    color=self.getEventColor("INFO"),
                )
            )

        self.updateCombinedJobs(remoteJobs)

    def saveListenerChannel(self) -> None:
        if self.listenerInput is None:
            return
        channel = self.listenerInput.text().strip()
        if not channel:
            QMessageBox.warning(
                self,
                "Invalid channel",
                "Channel cannot be empty.",
            )
            return
        if channel != self.listenerChannel:
            self.listenerChannel = channel
            self.updateListenerStatus()
            self.logEvent(
                ActivityEvent(
                    level="INFO",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Listening channel set to '{self.listenerChannel}'",
                    category="listener",
                    color=self.getEventColor("INFO"),
                )
            )

    def updateListenerStatus(self) -> None:
        if not (
            self.listenerStatusIndicator
            and self.listenerStatusLabel
            and self.listenerToggleButton
        ):
            return
        if self.isListening:
            self.listenerStatusIndicator.setStyleSheet("color: #34D399;")
            self.listenerStatusLabel.setText(
                f"Listening on {self.listenerChannel}"
            )
            self.listenerToggleButton.setText("Stop Listening")
        else:
            self.listenerStatusIndicator.setStyleSheet("color: #F87171;")
            self.listenerStatusLabel.setText("Not listening for jobs")
            self.listenerToggleButton.setText("Start Listening")

    def getEventColor(self, level: str) -> str:
        return {
            "INFO": "59, 130, 246",
            "SUCCESS": "52, 211, 153",
            "WARNING": "245, 158, 11",
            "ERROR": "248, 113, 113",
        }.get(level.upper(), "59, 130, 246")

    def samplePrinters(self) -> List[PrinterInfo]:
        return [
            PrinterInfo(
                printerName="Bambu X1C Lab",
                modelName="Bambu Lab X1 Carbon",
                ipAddress="192.168.0.189",
                serialNumber="Serial: BAM-002-1894",
                status="printing",
                statusDetail="Prototype Housing v2.2",
                statusColor="success",
            ),
            PrinterInfo(
                printerName="Prusa MK4 #1",
                modelName="Prusa MK4",
                ipAddress="192.168.0.204",
                serialNumber="Serial: PRU-004-2034",
                status="idle",
                statusDetail="Ready for next job",
                statusColor="warning",
            ),
            PrinterInfo(
                printerName="Ender 3 Workshop",
                modelName="Creality Ender 3 V2",
                ipAddress="192.168.0.198",
                serialNumber="Serial: CRE-002-2034",
                status="error",
                statusDetail="Nozzle temperature fault",
                statusColor="error",
            ),
        ]

    def sampleJobs(self) -> List[JobInfo]:
        return [
            JobInfo(
                jobNumber="#3",
                filename="bracket_mount.stl",
                targetPrinter="192.168.0.189",
                status="Queued",
                material="ABS",
                duration="90m",
                jobId=None,
                uploadedAt="09:25",
            ),
            JobInfo(
                jobNumber="#2",
                filename="phone_case_v2.stl",
                targetPrinter="192.168.0.189",
                status="Queued",
                material="PLA",
                duration="120m",
                jobId=None,
                uploadedAt="08:54",
            ),
            JobInfo(
                jobNumber="#1",
                filename="gear_assembly.stl",
                targetPrinter="192.168.0.204",
                status="Printing",
                material="PETG",
                duration="240m",
                jobId=None,
                uploadedAt="08:12",
            ),
        ]

    def sampleEvents(self) -> List[ActivityEvent]:
        return [
            ActivityEvent(
                level="WARNING",
                timestamp="09:32",
                message="Connection timeout with printer 'Ender 3 Workshop'",
                category="network",
                color="245, 158, 11",
            ),
            ActivityEvent(
                level="INFO",
                timestamp="09:18",
                message="Job queue updated - 3 jobs pending",
                category="system",
                color="59, 130, 246",
            ),
            ActivityEvent(
                level="SUCCESS",
                timestamp="08:42",
                message="Print bed temperature reached for Prusa MK4 #1",
                category="system",
                color="52, 211, 153",
            ),
            ActivityEvent(
                level="SUCCESS",
                timestamp="08:01",
                message="System started successfully",
                category="system",
                color="52, 211, 153",
            ),
        ]


def launch() -> None:
    application = QApplication.instance() or QApplication([])
    window = PrinterDashboardWindow()
    window.show()
    application.exec()


if __name__ == "__main__":
    launch()
