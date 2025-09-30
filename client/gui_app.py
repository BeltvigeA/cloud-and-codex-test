"""Graphical dashboard client for managing printers."""

from __future__ import annotations

import base64
import binascii
import gzip
import io
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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
    QPlainTextEdit,
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
    from client.client import (
        buildFetchUrl,
        buildPendingUrl,
        defaultBaseUrl,
        determineFilename,
    )
    from client.database import LocalDatabase, StoredJob, StoredJobMetadata
else:
    from .client import buildFetchUrl, buildPendingUrl, defaultBaseUrl, determineFilename
    from .database import LocalDatabase, StoredJob, StoredJobMetadata


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
    fetchToken: str | None = None


@dataclass
class RemoteJobMetadata:
    fetchToken: str
    unencryptedData: Dict[str, Any]
    decryptedData: Dict[str, Any]
    signedUrl: Optional[str]
    downloadedFilePath: Optional[Path] = None


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

        self.database = LocalDatabase()

        self.printers: List[PrinterInfo] = []
        self.jobs: List[JobInfo] = []
        self.sampleJobsList: List[JobInfo] = []
        self.manualJobs: List[JobInfo] = []
        self.remoteJobsList: List[JobInfo] = []
        self.currentRemoteJobIds: set[str] = set()
        self.remoteJobsSignature: tuple[str, ...] | None = None
        self.remoteJobDetails: Dict[str, RemoteJobMetadata] = {}
        self.metadataFetchFailures: Dict[str, datetime] = {}
        self.events = self.sampleEvents()
        self.keys: List[KeyInfo] = []
        self.listenerChannel = "user-123"
        self.knownListenerChannels = ["user-123", "production-queue", "lab-testing"]
        self.isListening = False
        self.jobCounter = 1
        self.backendBaseUrl = defaultBaseUrl
        self.listenerPollIntervalMs = 15000
        self.listenerPollTimer = QTimer(self)
        self.listenerPollTimer.setInterval(self.listenerPollIntervalMs)
        self.listenerPollTimer.timeout.connect(self.pollPendingJobs)
        self.listenerLastError: str | None = None

        self.loadPersistedState()

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
        self.listenerChannelCombo: QComboBox | None = None
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


    def loadPersistedState(self) -> None:
        storedPrinters = self.database.loadPrinters()
        if storedPrinters:
            self.printers = [
                PrinterInfo(
                    printerName=printer["printerName"],
                    modelName=printer["modelName"],
                    ipAddress=printer["ipAddress"],
                    serialNumber=printer["serialNumber"],
                    status=printer["status"],
                    statusDetail=printer["statusDetail"],
                    statusColor=printer["statusColor"],
                )
                for printer in storedPrinters
            ]
        else:
            self.printers = self.samplePrinters()
            for printer in self.printers:
                self.database.upsertPrinter(
                    serialNumber=printer.serialNumber,
                    printerName=printer.printerName,
                    modelName=printer.modelName,
                    ipAddress=printer.ipAddress,
                    status=printer.status,
                    statusDetail=printer.statusDetail,
                    statusColor=printer.statusColor,
                )

        storedJobs = self.database.loadJobs()
        self.manualJobs = []
        self.remoteJobsList = []
        for storedJob in storedJobs:
            job = JobInfo(
                jobNumber=storedJob.jobNumber,
                filename=storedJob.filename,
                targetPrinter=storedJob.targetPrinter,
                status=storedJob.status,
                material=storedJob.material,
                duration=storedJob.duration,
                jobId=storedJob.jobId,
                uploadedAt=storedJob.uploadedAt,
                fetchToken=storedJob.fetchToken,
            )
            if storedJob.source == "remote":
                self.remoteJobsList.append(job)
            else:
                self.manualJobs.append(job)

        if not storedJobs:
            self.sampleJobsList = self.sampleJobs()
            for job in self.sampleJobsList:
                if not job.jobId:
                    job.jobId = f"sample-{uuid.uuid4().hex}"
                if not job.jobNumber:
                    job.jobNumber = self.generateJobNumber()
                self.database.upsertJob(
                    StoredJob(
                        jobId=job.jobId,
                        source="sample",
                        jobNumber=job.jobNumber,
                        filename=job.filename,
                        targetPrinter=job.targetPrinter,
                        status=job.status,
                        material=job.material,
                        duration=job.duration,
                        uploadedAt=job.uploadedAt,
                        fetchToken=job.fetchToken,
                    )
                )
            self.manualJobs = list(self.sampleJobsList)

        self.currentRemoteJobIds = {job.jobId for job in self.remoteJobsList if job.jobId}
        self.updateManualJobCounter()
        self.jobs = [*self.remoteJobsList, *self.manualJobs]



    def updateManualJobCounter(self) -> None:
        highestNumber = 0
        for job in self.manualJobs:
            if job.jobNumber and job.jobNumber.startswith("#"):
                try:
                    highestNumber = max(highestNumber, int(job.jobNumber.lstrip("#")))
                except ValueError:
                    continue
        self.jobCounter = highestNumber + 1 if highestNumber else max(self.jobCounter, 1)


    def applyTheme(self) -> None:
        baseColor = "#F5F7FA"
        cardColor = "#FFFFFF"
        accentColor = "#2563EB"
        textColor = "#1F2937"
        mutedColor = "#6B7280"
        successColor = "#047857"
        warningColor = "#B45309"
        errorColor = "#B91C1C"

        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {baseColor};
                color: {textColor};
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: 14px;
            }}
            QLabel#logoTitle {{
                font-size: 20px;
                font-weight: 600;
                color: {textColor};
            }}
            QLabel#logoSubtitle {{
                color: {mutedColor};
                font-size: 13px;
            }}
            QListWidget {{
                background-color: transparent;
            }}
            QListWidget::item {{
                padding: 10px 14px;
                border-radius: 8px;
                color: {mutedColor};
            }}
            QListWidget::item:selected {{
                background-color: {accentColor};
                color: #FFFFFF;
            }}
            QListWidget::item:hover {{
                background-color: rgba(37, 99, 235, 0.12);
            }}
            QWidget#navigationPanel {{
                background-color: {cardColor};
                border-radius: 12px;
                padding: 24px 12px;
                border: 1px solid #E2E8F0;
            }}
            QFrame.card {{
                background-color: {cardColor};
                border-radius: 12px;
                padding: 20px;
                border: 1px solid #E2E8F0;
            }}
            QLabel.sectionTitle {{
                font-size: 18px;
                font-weight: 600;
                margin-bottom: 12px;
                color: {textColor};
            }}
            QLabel.metricTitle {{
                color: {mutedColor};
                font-size: 13px;
            }}
            QLabel.metricValue {{
                font-size: 30px;
                font-weight: 600;
                color: {textColor};
            }}
            QLabel.statusBadge {{
                font-size: 12px;
                font-weight: 600;
                padding: 4px 8px;
                border-radius: 12px;
            }}
            QLabel.statusSuccess {{
                background-color: rgba(4, 120, 87, 0.12);
                color: {successColor};
            }}
            QLabel.statusWarning {{
                background-color: rgba(180, 83, 9, 0.12);
                color: {warningColor};
            }}
            QLabel.statusError {{
                background-color: rgba(185, 28, 28, 0.12);
                color: {errorColor};
            }}
            QPushButton.primaryButton {{
                background-color: {accentColor};
                color: #FFFFFF;
                border-radius: 10px;
                padding: 8px 16px;
                font-weight: 600;
            }}
            QPushButton.primaryButton:hover {{
                background-color: #1D4ED8;
            }}
            QPushButton.primaryButton:pressed {{
                background-color: #1E40AF;
            }}
            QPushButton {{
                border-radius: 8px;
                padding: 6px 14px;
                background-color: #E5E7EB;
                color: {textColor};
                border: 1px solid transparent;
            }}
            QPushButton:hover {{
                background-color: #D1D5DB;
            }}
            QPushButton:pressed {{
                background-color: #CBD5E1;
            }}
            QLineEdit, QComboBox, QPlainTextEdit {{
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 8px;
                padding: 6px 10px;
                color: {textColor};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QScrollBar:vertical {{
                width: 10px;
                background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: #CBD5E1;
                border-radius: 5px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
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
            "Uploaded",
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
        self.listenerChannelCombo = QComboBox()
        self.listenerChannelCombo.setEditable(True)
        for channel in self.knownListenerChannels:
            self.listenerChannelCombo.addItem(channel)
        if self.listenerChannel not in self.knownListenerChannels:
            self.listenerChannelCombo.addItem(self.listenerChannel)
        self.listenerChannelCombo.setCurrentText(self.listenerChannel)
        formLayout.addRow("Recipient / Channel", self.listenerChannelCombo)
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

        eventsScrollArea = QScrollArea()
        eventsScrollArea.setWidgetResizable(True)
        eventsScrollArea.setFrameShape(QFrame.NoFrame)
        eventsScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        eventsContainer = QWidget()
        eventsContainerLayout = QVBoxLayout(eventsContainer)
        eventsContainerLayout.setSpacing(8)
        eventsContainerLayout.setContentsMargins(0, 0, 0, 0)

        eventsScrollArea.setWidget(eventsContainer)

        self.eventsLayout = eventsContainerLayout
        cardLayout.addWidget(eventsScrollArea)

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
        detailLabel.setStyleSheet("color: #6B7280;")
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
        metaLabel.setStyleSheet("color: #6B7280;")

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
                "font-weight: 600; color: #1F2937;"
                if header
                else "color: #4B5563;"
            )
            label.setMinimumWidth(100)
            layout.addWidget(label)

        layout.addStretch(1)
        return row

    def createEventLogRow(self, event: ActivityEvent) -> QWidget:
        row = QFrame()
        row.setFrameShape(QFrame.StyledPanel)
        row.setStyleSheet(
            "background-color: rgba(148, 163, 184, 0.12); border: 1px solid #E2E8F0; border-radius: 12px; padding: 12px;"
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
        messageLabel.setStyleSheet("color: #1F2937;")
        detailsLabel = QLabel(f"{event.timestamp} • {event.category}")
        detailsLabel.setStyleSheet("color: #6B7280;")

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
            emptyLabel.setStyleSheet("color: #6B7280;")
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
            emptyLabel.setStyleSheet("color: #6B7280;")
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
            placeholder.setStyleSheet("color: #6B7280;")
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
            self.database.upsertPrinter(
                serialNumber=printer.serialNumber,
                printerName=printer.printerName,
                modelName=printer.modelName,
                ipAddress=printer.ipAddress,
                status=printer.status,
                statusDetail=printer.statusDetail,
                statusColor=printer.statusColor,
            )
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
            self.database.deletePrinter(printer.serialNumber)
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
        return f"manual-{uuid.uuid4().hex}"

    def createJobRow(self, job: JobInfo) -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            "background-color: rgba(148, 163, 184, 0.12); border: 1px solid #E2E8F0; border-radius: 12px; padding: 10px;"
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
            job.uploadedAt or "-",
        ]:
            label = QLabel(value)
            label.setStyleSheet("color: #1F2937;")
            label.setMinimumWidth(100)
            layout.addWidget(label)

        layout.addStretch(1)
        if job.fetchToken:
            dataButton = QPushButton("View Data")
            dataButton.clicked.connect(partial(self.showRemoteJobData, job))
            layout.addWidget(dataButton)
            downloadButton = QPushButton("Download")
            downloadButton.clicked.connect(partial(self.downloadRemoteJobFile, job))
            layout.addWidget(downloadButton)
        else:
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

    def showRemoteJobData(self, job: JobInfo) -> None:
        metadata = self.fetchRemoteJobData(job, triggerRefresh=True)
        if metadata is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Incoming File Details")
        dialog.setMinimumWidth(520)
        dialogLayout = QVBoxLayout(dialog)
        dialogLayout.setSpacing(16)

        headerForm = QFormLayout()
        headerForm.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        headerForm.addRow("Filename", QLabel(job.filename))
        headerForm.addRow("Uploaded", QLabel(job.uploadedAt or "-"))
        headerForm.addRow("Printer", QLabel(job.targetPrinter or "-"))
        headerForm.addRow("Status", QLabel(job.status or "Pending"))
        dialogLayout.addLayout(headerForm)

        relevantPairs = self.buildRelevantFieldPairs(metadata.unencryptedData)
        if relevantPairs:
            relevantFrame = QFrame()
            relevantFrame.setFrameShape(QFrame.NoFrame)
            relevantLayout = QFormLayout(relevantFrame)
            relevantLayout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            for labelText, valueText in relevantPairs:
                valueLabel = QLabel(valueText)
                valueLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
                relevantLayout.addRow(labelText, valueLabel)
            dialogLayout.addWidget(relevantFrame)

        imageBase64 = self.extractBase64Image(metadata.unencryptedData)
        if imageBase64:
            imagePixmap = self.decodeBase64Image(imageBase64)
            if imagePixmap is not None:
                imageLabel = QLabel()
                imageLabel.setAlignment(Qt.AlignCenter)
                imageLabel.setPixmap(
                    imagePixmap.scaledToWidth(320, Qt.SmoothTransformation)
                )
                dialogLayout.addWidget(imageLabel)

        sensitivePairs = self.decodeSensitiveData(metadata.decryptedData)
        sensitiveFrame = QFrame()
        sensitiveFrame.setFrameShape(QFrame.NoFrame)
        sensitiveLayout = QFormLayout(sensitiveFrame)
        sensitiveLayout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sensitiveTitle = QLabel("Sensitive data")
        sensitiveTitle.setStyleSheet("font-weight: 600; margin-bottom: 4px;")
        dialogLayout.addWidget(sensitiveTitle)
        for labelText, valueText in sensitivePairs:
            valueLabel = QLabel(valueText)
            valueLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
            sensitiveLayout.addRow(labelText, valueLabel)
        dialogLayout.addWidget(sensitiveFrame)

        toggleButton = QPushButton("Show All Data")
        dialogLayout.addWidget(toggleButton)
        allDataView = QPlainTextEdit()
        allDataView.setReadOnly(True)
        allDataView.setPlainText(self.formatJsonForDisplay(metadata))
        allDataView.hide()
        dialogLayout.addWidget(allDataView)

        def toggleFullData() -> None:
            if allDataView.isVisible():
                allDataView.hide()
                toggleButton.setText("Show All Data")
            else:
                allDataView.show()
                toggleButton.setText("Hide All Data")

        toggleButton.clicked.connect(toggleFullData)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Close)
        buttonBox.rejected.connect(dialog.reject)
        buttonBox.accepted.connect(dialog.accept)
        buttonBox.button(QDialogButtonBox.Close).setText("Close")
        dialogLayout.addWidget(buttonBox)

        dialog.exec()

    def downloadRemoteJobFile(self, job: JobInfo) -> None:
        metadata = self.fetchRemoteJobData(job, triggerRefresh=True)
        if metadata is None:
            return
        if metadata.signedUrl is None:
            QMessageBox.warning(
                self,
                "Download unavailable",
                "The server did not provide a download link for this file.",
            )
            return

        cacheKey = self.getJobCacheKey(job)
        defaultDirectory = Path.home() / "Downloads"
        if not defaultDirectory.exists():
            defaultDirectory = Path.home()
        selectedDirectory = QFileDialog.getExistingDirectory(
            self,
            "Choose download folder",
            str(defaultDirectory),
        )
        if not selectedDirectory:
            return

        targetDirectory = Path(selectedDirectory)
        try:
            response = requests.get(metadata.signedUrl, stream=True, timeout=60)
            response.raise_for_status()
        except RequestException as error:
            QMessageBox.warning(
                self,
                "Download failed",
                f"Unable to download the file: {error}",
            )
            return

        fallbackName = Path(job.filename).name if job.filename else "downloaded_file.bin"
        filename = determineFilename(response, fallbackName=fallbackName)
        filename = Path(filename).name or fallbackName
        downloadPath = self.createUniqueDownloadPath(targetDirectory, filename)

        try:
            with open(downloadPath, "wb") as fileHandle:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        fileHandle.write(chunk)
        except OSError as error:
            QMessageBox.warning(
                self,
                "Unable to save file",
                f"Could not save the downloaded file: {error}",
            )
            return

        metadata.downloadedFilePath = downloadPath
        self.remoteJobDetails[cacheKey] = metadata
        if job.jobId:
            self.database.saveJobMetadata(
                jobId=job.jobId,
                fetchToken=metadata.fetchToken,
                unencryptedData=metadata.unencryptedData,
                decryptedData=metadata.decryptedData,
                signedUrl=metadata.signedUrl,
                downloadedFilePath=str(downloadPath),
            )

        self.logEvent(
            ActivityEvent(
                level="SUCCESS",
                timestamp=datetime.now().strftime("%H:%M"),
                message=f"Downloaded '{filename}'",
                category="downloads",
                color=self.getEventColor("SUCCESS"),
            )
        )
        QMessageBox.information(
            self,
            "Download complete",
            f"File saved to {downloadPath}",
        )

    def refreshJobsTable(self) -> None:
        if self.jobsContainerLayout is None:
            return
        self.clearLayout(self.jobsContainerLayout)
        if not self.jobs:
            placeholder = QLabel("No jobs in the queue yet")
            placeholder.setStyleSheet("color: #6B7280;")
            self.jobsContainerLayout.addWidget(placeholder)
            return
        for job in reversed(self.jobs):
            self.jobsContainerLayout.addWidget(self.createJobRow(job))

    def loadRemoteJobMetadataFromCache(self, job: JobInfo) -> Optional[RemoteJobMetadata]:
        cacheKey = self.getJobCacheKey(job)
        cached = self.remoteJobDetails.get(cacheKey)
        if cached is not None:
            return cached
        storedMetadata = self.database.loadJobMetadata(job.jobId, job.fetchToken)
        if storedMetadata is None:
            return None
        storedPath = (
            Path(storedMetadata.downloadedFilePath)
            if storedMetadata.downloadedFilePath
            else None
        )
        metadata = RemoteJobMetadata(
            fetchToken=storedMetadata.fetchToken or job.fetchToken or "",
            unencryptedData=storedMetadata.unencryptedData,
            decryptedData=storedMetadata.decryptedData,
            signedUrl=storedMetadata.signedUrl,
            downloadedFilePath=storedPath,
        )
        self.remoteJobDetails[cacheKey] = metadata
        return metadata

    def requestRemoteJobMetadata(
        self,
        job: JobInfo,
        *,
        silent: bool = False,
        logEventOnSuccess: bool = True,
    ) -> Optional[RemoteJobMetadata]:
        cacheKey = self.getJobCacheKey(job)

        if not job.fetchToken:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Missing token",
                    "This job does not include a fetch token and cannot be retrieved.",
                )
            return None
        try:
            fetchUrl = buildFetchUrl(self.backendBaseUrl, job.fetchToken)
        except ValueError as error:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Invalid configuration",
                    f"Unable to build fetch URL: {error}",
                )
            return None

        try:
            response = requests.get(fetchUrl, timeout=30)
            response.raise_for_status()
        except RequestException as error:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Fetch failed",
                    f"Unable to retrieve job metadata: {error}",
                )
            self.metadataFetchFailures[cacheKey] = datetime.utcnow()
            return None

        try:
            payload = response.json()
        except ValueError:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Unexpected response",
                    "The server returned data in an unexpected format.",
                )
            self.metadataFetchFailures[cacheKey] = datetime.utcnow()
            return None

        if not isinstance(payload, dict):
            if not silent:
                QMessageBox.warning(
                    self,
                    "Unexpected response",
                    "The server returned data in an unexpected structure.",
                )
            self.metadataFetchFailures[cacheKey] = datetime.utcnow()
            return None

        unencryptedData = self.normalizeDataDict(payload.get("unencryptedData"))
        decryptedData = self.normalizeDataDict(payload.get("decryptedData"))
        signedUrl = payload.get("signedUrl")
        signedUrlValue = signedUrl if isinstance(signedUrl, str) and signedUrl else None

        metadata = RemoteJobMetadata(
            fetchToken=job.fetchToken,
            unencryptedData=unencryptedData,
            decryptedData=decryptedData,
            signedUrl=signedUrlValue,
        )
        self.remoteJobDetails[cacheKey] = metadata
        if job.jobId:
            self.database.saveJobMetadata(
                jobId=job.jobId,
                fetchToken=job.fetchToken,
                unencryptedData=unencryptedData,
                decryptedData=decryptedData,
                signedUrl=signedUrlValue,
            )
        self.metadataFetchFailures.pop(cacheKey, None)

        if logEventOnSuccess:
            self.logEvent(
                ActivityEvent(
                    level="INFO",
                    timestamp=datetime.now().strftime("%H:%M"),
                    message=f"Fetched metadata for '{job.filename}'",
                    category="jobs",
                    color=self.getEventColor("INFO"),
                )
            )
        return metadata

    def fetchRemoteJobData(
        self,
        job: JobInfo,
        *,
        silent: bool = False,
        logEventOnSuccess: bool = True,
        triggerRefresh: bool = False,
    ) -> Optional[RemoteJobMetadata]:
        metadata = self.loadRemoteJobMetadataFromCache(job)
        if metadata is not None:
            return metadata
        metadata = self.requestRemoteJobMetadata(
            job,
            silent=silent,
            logEventOnSuccess=logEventOnSuccess,
        )
        if metadata is not None and triggerRefresh:
            self.pollPendingJobs(force=True)
        return metadata

    def getJobCacheKey(self, job: JobInfo) -> str:
        if job.fetchToken:
            return job.fetchToken
        if job.jobId:
            return job.jobId
        return job.filename

    def preloadRemoteJobMetadata(self, remoteJobs: List[JobInfo]) -> None:
        if not remoteJobs:
            return
        now = datetime.utcnow()
        retryDelay = timedelta(minutes=5)
        for job in remoteJobs:
            cacheKey = self.getJobCacheKey(job)
            if not job.fetchToken:
                continue
            if self.loadRemoteJobMetadataFromCache(job) is not None:
                continue
            lastFailure = self.metadataFetchFailures.get(cacheKey)
            if lastFailure and now - lastFailure < retryDelay:
                continue
            metadata = self.fetchRemoteJobData(
                job,
                silent=True,
                logEventOnSuccess=False,
                triggerRefresh=False,
            )
            if metadata is None:
                self.metadataFetchFailures[cacheKey] = now
            else:
                self.metadataFetchFailures.pop(cacheKey, None)

    def normalizeDataDict(self, rawData: object) -> Dict[str, Any]:
        if isinstance(rawData, dict):
            return rawData
        if isinstance(rawData, str):
            try:
                parsed = json.loads(rawData)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def decodeSensitiveData(self, rawData: Dict[str, Any]) -> List[tuple[str, str]]:
        unpacked = self.unpackSensitiveData(rawData)
        fieldOrder = [
            ("printerAccessCode", "Tilgangskode"),
            ("printerSerialNumber", "Serienummer"),
            ("printerIpAddress", "IP-adresse"),
            ("useAms", "AMS-bruk"),
        ]
        values: List[tuple[str, str]] = []
        for key, label in fieldOrder:
            displayValue = self.formatDisplayValue(unpacked.get(key))
            values.append((label, displayValue))
        return values

    def unpackSensitiveData(self, rawData: Dict[str, Any]) -> Dict[str, Any]:
        if not rawData:
            return {}
        data = dict(rawData)
        encodedCandidates = [
            data.get("base64Gzipped"),
            data.get("base64Compressed"),
            data.get("encodedPayload"),
        ]
        for candidate in encodedCandidates:
            if isinstance(candidate, str) and candidate.strip():
                decoded = self.decodeBase64GzipJson(candidate)
                if decoded is not None:
                    data.update(decoded)
        nested = data.get("sensitiveData")
        if isinstance(nested, dict):
            data.update(nested)
        return data

    def decodeBase64GzipJson(self, encoded: str) -> Optional[Dict[str, Any]]:
        try:
            binary = base64.b64decode(encoded)
        except binascii.Error:
            return None
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(binary)) as gzipFile:
                decodedBytes = gzipFile.read()
        except OSError:
            return None
        try:
            decodedText = decodedBytes.decode("utf-8")
        except UnicodeDecodeError:
            return None
        try:
            parsed = json.loads(decodedText)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def extractBase64Image(self, unencryptedData: Dict[str, Any]) -> Optional[str]:
        imageKeys = [
            "base64GatedImage",
            "base64PreviewImage",
            "previewImageBase64",
            "imageBase64",
        ]
        for key in imageKeys:
            value = unencryptedData.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key, value in unencryptedData.items():
            if not isinstance(value, str):
                continue
            lowerKey = key.lower()
            if "image" in lowerKey and "base64" in lowerKey and value.strip():
                return value.strip()
        return None

    def decodeBase64Image(self, encoded: str) -> Optional[QPixmap]:
        try:
            imageBytes = base64.b64decode(encoded)
        except binascii.Error:
            return None
        image = QImage.fromData(imageBytes)
        if image.isNull():
            return None
        return QPixmap.fromImage(image)

    def buildRelevantFieldPairs(self, data: Dict[str, Any]) -> List[tuple[str, str]]:
        if not data:
            return []
        if data.get("__snapshot__"):
            summaryPairs = self.buildPairsFromMapping(data.get("jobSummary"))
            if summaryPairs:
                return summaryPairs
            return self.buildPairsFromMapping(data.get("cloudPayload"))
        return self.buildPairsFromMapping(data)

    def buildPairsFromMapping(self, mapping: Any) -> List[tuple[str, str]]:
        if not isinstance(mapping, dict):
            return []
        preferredFields = [
            ("productName", "Produkt"),
            ("objectName", "Objekt"),
            ("priority", "Prioritet"),
            ("quantity", "Antall"),
            ("filamentType", "Filamenttype"),
            ("filamentColor", "Filamentfarge"),
            ("filamentUsedGrams", "Filament (gram)"),
            ("layerHeight", "Lagtykkelse"),
            ("nozzleSize", "Dysetykkelse"),
        ]
        pairs: List[tuple[str, str]] = []
        for key, label in preferredFields:
            if key in mapping:
                pairs.append((label, self.formatDisplayValue(mapping.get(key))))
        if pairs:
            return pairs
        fallbackPairs: List[tuple[str, str]] = []
        for index, (key, value) in enumerate(mapping.items()):
            if index >= 6:
                break
            if isinstance(value, (str, int, float, bool)):
                fallbackPairs.append((self.humanizeKey(key), self.formatDisplayValue(value)))
        return fallbackPairs

    def humanizeKey(self, key: str) -> str:
        spaced = re.sub(r"([A-Z])", r" \1", key)
        spaced = spaced.replace("_", " ").strip()
        if not spaced:
            return key
        return spaced[0].upper() + spaced[1:]

    def formatDisplayValue(self, value: Any) -> str:
        if isinstance(value, bool):
            return "Ja" if value else "Nei"
        if isinstance(value, float):
            return f"{value:.2f}".rstrip("0").rstrip(".")
        if value in (None, ""):
            return "-"
        return str(value)

    def formatJsonForDisplay(self, metadata: RemoteJobMetadata) -> str:
        combined = {
            "unencryptedData": metadata.unencryptedData,
            "decryptedData": metadata.decryptedData,
        }
        return json.dumps(combined, indent=2, ensure_ascii=False)

    def createUniqueDownloadPath(self, directory: Path, filename: str) -> Path:
        sanitizedName = filename or "downloaded_file.bin"
        targetPath = directory / sanitizedName
        counter = 1
        while targetPath.exists():
            stem = targetPath.stem
            suffix = targetPath.suffix
            targetPath = directory / f"{stem}_{counter}{suffix}"
            counter += 1
        return targetPath

    def buildJobSummary(self, job: JobInfo) -> Dict[str, Any]:
        return {
            "jobNumber": job.jobNumber,
            "filename": job.filename,
            "targetPrinter": job.targetPrinter,
            "status": job.status,
            "material": job.material,
            "duration": job.duration,
            "uploadedAt": job.uploadedAt,
            "jobId": job.jobId,
            "fetchToken": job.fetchToken,
        }

    def sanitizeForJson(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self.sanitizeForJson(val) for key, val in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self.sanitizeForJson(item) for item in value]
        return str(value)

    def cacheRemoteJobSnapshot(self, job: JobInfo, payload: Any) -> None:
        if not isinstance(payload, dict) or not job.jobId:
            return
        existingMetadata = self.loadRemoteJobMetadataFromCache(job)
        if existingMetadata is not None and not existingMetadata.unencryptedData.get("__snapshot__", False):
            return
        sanitizedPayload = self.sanitizeForJson(payload)
        if not isinstance(sanitizedPayload, dict):
            sanitizedPayload = {"value": sanitizedPayload}
        summaryData = self.sanitizeForJson(self.buildJobSummary(job))
        summaryDict = summaryData if isinstance(summaryData, dict) else {}
        combinedData: Dict[str, Any] = {
            "__snapshot__": True,
            "jobSummary": summaryDict,
            "cloudPayload": sanitizedPayload,
        }
        metadata = RemoteJobMetadata(
            fetchToken=job.fetchToken or "",
            unencryptedData=combinedData,
            decryptedData={},
            signedUrl=None,
            downloadedFilePath=None,
        )
        cacheKey = self.getJobCacheKey(job)
        self.remoteJobDetails[cacheKey] = metadata
        self.database.saveJobMetadata(
            jobId=job.jobId,
            fetchToken=job.fetchToken,
            unencryptedData=combinedData,
            decryptedData={},
            signedUrl=None,
            downloadedFilePath=None,
        )

    def addManualJob(self, job: JobInfo) -> None:
        if not job.jobId:
            job.jobId = self.generateManualJobId()
        if not job.jobNumber:
            job.jobNumber = self.generateJobNumber()
        if not job.uploadedAt:
            job.uploadedAt = datetime.now().strftime("%H:%M")
        self.manualJobs = [existing for existing in self.manualJobs if existing.jobId != job.jobId]
        self.manualJobs.insert(0, job)
        self.database.upsertJob(
            StoredJob(
                jobId=job.jobId,
                source="manual",
                jobNumber=job.jobNumber,
                filename=job.filename,
                targetPrinter=job.targetPrinter,
                status=job.status,
                material=job.material,
                duration=job.duration,
                uploadedAt=job.uploadedAt,
                fetchToken=job.fetchToken,
            )
        )
        self.updateManualJobCounter()
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
            if job.jobId:
                self.database.deleteJob(job.jobId)
        elif job in self.jobs:
            self.jobs.remove(job)
            if job.jobId:
                self.database.deleteJob(job.jobId)
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
            "background-color: rgba(148, 163, 184, 0.12); border: 1px solid #E2E8F0; border-radius: 12px; padding: 12px;"
        )
        layout = QHBoxLayout(row)
        layout.setSpacing(12)

        label = QLabel(key.keyLabel)
        label.setStyleSheet("font-weight: 600; color: #1F2937;")
        valueLabel = QLabel(key.keyValue)
        valueLabel.setStyleSheet(
            "color: #1F2937; font-family: 'JetBrains Mono', 'Fira Code', monospace;"
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
            placeholder.setStyleSheet("color: #6B7280; font-size: 16px;")
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
            placeholder.setStyleSheet("color: #6B7280;")
            self.eventsLayout.addWidget(placeholder)
            return
        for event in reversed(self.events):
            self.eventsLayout.addWidget(self.createEventLogRow(event))
        self.eventsLayout.addStretch(1)

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
        if self.listenerChannelCombo is not None:
            selectedChannel = self.listenerChannelCombo.currentText().strip()
            if not selectedChannel:
                QMessageBox.warning(
                    self,
                    "Invalid channel",
                    "Channel cannot be empty when starting the listener.",
                )
                return
            if selectedChannel != self.listenerChannel:
                self.listenerChannel = selectedChannel
                if selectedChannel not in self.knownListenerChannels:
                    self.knownListenerChannels.append(selectedChannel)
                    self.listenerChannelCombo.addItem(selectedChannel)
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

        remoteJobEntries: List[Tuple[JobInfo, Dict[str, Any]]] = []
        for rawIndex, pending in enumerate(pendingFiles, start=1):
            if not isinstance(pending, dict):
                continue
            jobIdSource = pending.get("fileId") or pending.get("fetchToken") or rawIndex
            jobId = str(jobIdSource)
            fetchToken = pending.get("fetchToken")
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
            jobInfo = JobInfo(
                jobNumber="",
                filename=str(filename),
                targetPrinter=str(targetPrinter),
                status=statusDisplay,
                material=str(materialValue),
                duration=self.formatJobDuration(durationValue),
                jobId=jobId,
                uploadedAt=uploadedAt,
                fetchToken=str(fetchToken) if isinstance(fetchToken, str) else None,
            )
            remoteJobEntries.append((jobInfo, pending))

        remoteJobEntries.sort(key=lambda entry: entry[0].uploadedAt or "", reverse=True)
        for index, (job, payload) in enumerate(remoteJobEntries, start=1):
            job.jobNumber = f"#{index}"
            self.cacheRemoteJobSnapshot(job, payload)

        remoteJobs = [job for job, _ in remoteJobEntries]

        for job in remoteJobs:
            self.database.upsertJob(
                StoredJob(
                    jobId=job.jobId or f"remote-{uuid.uuid4().hex}",
                    source="remote",
                    jobNumber=job.jobNumber,
                    filename=job.filename,
                    targetPrinter=job.targetPrinter,
                    status=job.status,
                    material=job.material,
                    duration=job.duration,
                    uploadedAt=job.uploadedAt,
                    fetchToken=job.fetchToken,
                )
            )
        self.database.pruneJobs("remote", (job.jobId for job in remoteJobs if job.jobId))

        for job in remoteJobs:
            self.database.upsertJob(
                StoredJob(
                    jobId=job.jobId or f"remote-{uuid.uuid4().hex}",
                    source="remote",
                    jobNumber=job.jobNumber,
                    filename=job.filename,
                    targetPrinter=job.targetPrinter,
                    status=job.status,
                    material=job.material,
                    duration=job.duration,
                    uploadedAt=job.uploadedAt,
                    fetchToken=job.fetchToken,
                )
            )
        self.database.pruneJobs("remote", (job.jobId for job in remoteJobs if job.jobId))

        self.currentRemoteJobIds = {job.jobId for job in remoteJobs if job.jobId}

        if self.sampleJobsList:
            for sampleJob in self.sampleJobsList:
                if sampleJob.jobId:
                    self.database.deleteJob(sampleJob.jobId)
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

        self.preloadRemoteJobMetadata(remoteJobs)
        self.updateCombinedJobs(remoteJobs)

    def saveListenerChannel(self) -> None:
        if self.listenerChannelCombo is None:
            return
        channel = self.listenerChannelCombo.currentText().strip()
        if not channel:
            QMessageBox.warning(
                self,
                "Invalid channel",
                "Channel cannot be empty.",
            )
            return
        if channel != self.listenerChannel:
            self.listenerChannel = channel
            if channel not in self.knownListenerChannels:
                self.knownListenerChannels.append(channel)
                if self.listenerChannelCombo is not None:
                    self.listenerChannelCombo.addItem(channel)
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
