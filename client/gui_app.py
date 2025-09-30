"""Graphical dashboard client for managing printers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


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


@dataclass
class ActivityEvent:
    level: str
    timestamp: str
    message: str
    category: str
    color: str


class NavigationList(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSpacing(6)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setFixedWidth(200)

    def addDestination(self, label: str) -> None:
        item = QListWidgetItem(label)
        sizeHint = item.sizeHint()
        sizeHint.setHeight(sizeHint.height() + 8)
        item.setSizeHint(sizeHint)
        self.addItem(item)


class PrinterDashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PrintMaster Dashboard")
        self.resize(1280, 830)

        self.mainWidget = QWidget()
        self.setCentralWidget(self.mainWidget)

        rootLayout = QHBoxLayout(self.mainWidget)
        rootLayout.setContentsMargins(24, 24, 24, 24)
        rootLayout.setSpacing(24)

        self.navigationList = NavigationList()
        self.navigationList.addDestination("Dashboard")
        self.navigationList.addDestination("Printers")
        self.navigationList.addDestination("Job Queue")
        self.navigationList.addDestination("Keys")
        self.navigationList.addDestination("Events")

        self.navigationList.currentRowChanged.connect(self.changePage)

        navigationWrapper = QVBoxLayout()
        navigationWrapper.setContentsMargins(0, 0, 0, 0)
        navigationWrapper.setSpacing(16)

        logoWrapper = self.createLogoHeader()
        navigationWrapper.addWidget(logoWrapper)
        navigationWrapper.addWidget(self.navigationList)
        navigationWrapper.addStretch(1)
        navigationWrapperWidget = QWidget()
        navigationWrapperWidget.setLayout(navigationWrapper)
        navigationWrapperWidget.setFixedWidth(220)
        navigationWrapperWidget.setObjectName("navigationPanel")

        self.pageStack = QStackedWidget()

        self.dashboardPage = self.createDashboardPage()
        self.printersPage = self.createPrintersPage()
        self.jobQueuePage = self.createJobQueuePage()
        self.keysPage = self.createKeysPage()
        self.eventsPage = self.createEventsPage()

        for page in [
            self.dashboardPage,
            self.printersPage,
            self.jobQueuePage,
            self.keysPage,
            self.eventsPage,
        ]:
            self.pageStack.addWidget(page)

        rootLayout.addWidget(navigationWrapperWidget)
        rootLayout.addWidget(self.pageStack, 1)

        self.applyTheme()
        self.navigationList.setCurrentRow(0)

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

    def createDashboardPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        metricCard = QFrame()
        metricCard.setObjectName("metricsCard")
        metricCard.setProperty("class", "card")
        metricLayout = QHBoxLayout(metricCard)
        metricLayout.setSpacing(32)

        for title, value in [
            ("Total Printers", "3"),
            ("Active Jobs", "3"),
            ("Queued Jobs", "2"),
            ("Online Printers", "3"),
        ]:
            card = self.createMetricWidget(title, value)
            metricLayout.addWidget(card)

        printerStatusCard = QFrame()
        printerStatusCard.setProperty("class", "card")
        printerStatusLayout = QVBoxLayout(printerStatusCard)
        printerStatusLayout.setSpacing(16)

        printerStatusTitle = QLabel("Printer Status")
        printerStatusTitle.setProperty("class", "sectionTitle")
        printerStatusLayout.addWidget(printerStatusTitle)

        for printer in self.samplePrinters():
            statusRow = self.createPrinterStatusRow(printer)
            printerStatusLayout.addWidget(statusRow)

        printerStatusLayout.addStretch(1)

        recentActivityCard = QFrame()
        recentActivityCard.setProperty("class", "card")
        recentActivityLayout = QVBoxLayout(recentActivityCard)
        recentActivityLayout.setSpacing(16)

        recentActivityTitle = QLabel("Recent Activity")
        recentActivityTitle.setProperty("class", "sectionTitle")
        recentActivityLayout.addWidget(recentActivityTitle)

        for event in self.sampleEvents():
            eventWidget = self.createActivityRow(event)
            recentActivityLayout.addWidget(eventWidget)

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
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addButton)

        layout.addLayout(headerRow)

        grid = QGridLayout()
        grid.setSpacing(20)

        for index, printer in enumerate(self.samplePrinters()):
            printerCard = self.createPrinterCard(printer)
            row = index // 2
            column = index % 2
            grid.addWidget(printerCard, row, column)

        layout.addLayout(grid)
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
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addButton)

        layout.addLayout(headerRow)

        jobsCard = QFrame()
        jobsCard.setProperty("class", "card")
        jobsLayout = QVBoxLayout(jobsCard)
        jobsLayout.setSpacing(12)

        headerLabels = ["#", "Filename", "Target Printer", "Status", "Material", "Duration"]
        headerRowWidget = self.createTableRow(headerLabels, header=True)
        jobsLayout.addWidget(headerRowWidget)

        for job in self.sampleJobs():
            jobsLayout.addWidget(
                self.createTableRow(
                    [
                        job.jobNumber,
                        job.filename,
                        job.targetPrinter,
                        job.status,
                        job.material,
                        job.duration,
                    ]
                )
            )

        layout.addWidget(jobsCard)
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
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(addButton)

        layout.addLayout(headerRow)

        emptyCard = QFrame()
        emptyCard.setProperty("class", "card")
        emptyLayout = QVBoxLayout(emptyCard)
        emptyLayout.setSpacing(12)
        emptyLayout.setAlignment(Qt.AlignCenter)

        iconLabel = QLabel("🔑")
        iconLabel.setAlignment(Qt.AlignCenter)
        iconLabel.setFont(QFont("Segoe UI Emoji", 40))
        messageLabel = QLabel("No public keys configured yet")
        messageLabel.setAlignment(Qt.AlignCenter)
        messageLabel.setStyleSheet("color: #7B8AA5; font-size: 16px;")
        ctaButton = QPushButton("Add Your First Key")
        ctaButton.setProperty("class", "primaryButton")

        emptyLayout.addWidget(iconLabel)
        emptyLayout.addWidget(messageLabel)
        emptyLayout.addWidget(ctaButton)

        layout.addWidget(emptyCard, alignment=Qt.AlignCenter)
        layout.addStretch(1)

        return page

    def createEventsPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        headerRow = QHBoxLayout()
        title = QLabel("System Events")
        title.setProperty("class", "sectionTitle")
        refreshButton = QPushButton("Refresh")
        refreshButton.setProperty("class", "primaryButton")
        headerRow.addWidget(title)
        headerRow.addStretch(1)
        headerRow.addWidget(refreshButton)

        layout.addLayout(headerRow)

        eventsCard = QFrame()
        eventsCard.setProperty("class", "card")
        eventsLayout = QVBoxLayout(eventsCard)
        eventsLayout.setSpacing(12)

        logTitle = QLabel("Event Log")
        logTitle.setProperty("class", "sectionTitle")
        eventsLayout.addWidget(logTitle)

        for event in self.sampleEvents():
            eventsLayout.addWidget(self.createEventLogRow(event))

        layout.addWidget(eventsCard)
        layout.addStretch(1)

        return page

    def changePage(self, index: int) -> None:
        self.pageStack.setCurrentIndex(index)

    def createMetricWidget(self, title: str, value: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)

        titleLabel = QLabel(title)
        titleLabel.setProperty("class", "metricTitle")
        valueLabel = QLabel(value)
        valueLabel.setProperty("class", "metricValue")

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

        return row

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
            ),
            JobInfo(
                jobNumber="#2",
                filename="phone_case_v2.stl",
                targetPrinter="192.168.0.189",
                status="Queued",
                material="PLA",
                duration="120m",
            ),
            JobInfo(
                jobNumber="#1",
                filename="gear_assembly.stl",
                targetPrinter="192.168.0.204",
                status="Printing",
                material="PETG",
                duration="240m",
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
