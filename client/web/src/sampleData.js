export const samplePendingJobs = [
  {
    channelId: 'user-123',
    encryptedData: {
      encryptedBlobHex:
        '0a2400c2167a4735184fb7abd54bce8b66abc46e1f18d1a3e1e35bce52f7bd067e6e7e926b47f049fbb6b1669f3e2fcb837cbb8ad4c31f9f8513a6d9522f',
      fetchToken: 'HufPmh0FUYKa6wR7c_9D7vWdeMIxvwpdAUWDm',
      fetchTokenConsumed: false,
      fetchTokenExpiresAt: 'September 30, 2023 at 11:38:22 AM UTC+2',
      gcsPath: 'user-123/3d/bed/f45e-4456-b725-83759332_mammut_3mf',
      originalFilename: 'mammut.3mf',
      recipientId: 'user-123',
      status: 'uploaded',
      timestamp: 'September 30, 2023 at 10:57:38 AM UTC+2'
    },
    unencryptedData: {
      base64ImageCode:
        'bVBORw0KGgoAAAANSUhEUgAAAAUA' +
        'AAAFCAYAAACNbyblAAAAHElEQVQI12P4' +
        '9/wPDAwMDAyMRgYGBgYGABDAAOnb8qBAAAAAElFTkSuQmCC',
      filamentColor: 'Hvit',
      filamentType: 'PLA',
      infillDensity: 15,
      layerHeight: 0.2,
      nozzleSize: 0.4,
      objectHeight: 50,
      printJobId: '68db9f70e53e4aa0a8aac13e4',
      printJobSeconds: 19889,
      priority: 'Normal',
      uploadedBy: 'test@company.no',
      threeDPrinter: 'Nye printer'
    }
  }
];

export function findPendingJobByChannel(channelId) {
  return samplePendingJobs.find(
    (pendingJob) => pendingJob.channelId.toLowerCase() === channelId.toLowerCase()
  );
}
