import React, { useEffect, useMemo, useRef, useState } from 'react';
import ChannelListener from './components/ChannelListener.jsx';
import DataSection from './components/DataSection.jsx';
import { findPendingJobByChannel } from './sampleData.js';

const listenerDelayMs = 900;

function App() {
  const [listenerState, setListenerState] = useState('idle');
  const [activeChannel, setActiveChannel] = useState(null);
  const [payload, setPayload] = useState(null);
  const [errorMessage, setErrorMessage] = useState('');
  const timeoutRef = useRef(null);

  useEffect(() => () => clearTimeout(timeoutRef.current), []);

  const handleChannelSubmit = (channelId) => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }

    setActiveChannel(channelId);
    setListenerState('listening');
    setPayload(null);
    setErrorMessage('');

    timeoutRef.current = setTimeout(() => {
      const match = findPendingJobByChannel(channelId);
      if (match) {
        setPayload(match);
        setListenerState('received');
      } else {
        setListenerState('error');
        setErrorMessage(`No pending uploads were found for channel "${channelId}".`);
      }
    }, listenerDelayMs);
  };

  const encryptedData = useMemo(() => payload?.encryptedData ?? null, [payload]);
  const unencryptedData = useMemo(() => payload?.unencryptedData ?? null, [payload]);

  return (
    <main className="appContainer">
      <header className="headerSection">
        <h1 className="headerTitle">PrintMaster Dashboard</h1>
        <p className="headerSubtitle">
          Lightweight React demo that simulates the channel-based listener from the original desktop
          dashboard. Provide a channel identifier and the interface will surface any pending job
          metadata when data becomes available.
        </p>
      </header>

      <ChannelListener
        onChannelSubmit={handleChannelSubmit}
        listenerState={listenerState}
        errorMessage={errorMessage}
      />

      <section className="listenerCard" aria-live="polite">
        <h2>Latest payload</h2>
        {listenerState === 'listening' && <p>Waiting for data from {activeChannel}…</p>}
        {listenerState === 'received' && payload ? (
          <div className="dataGrid">
            <DataSection title="Encrypted metadata" data={encryptedData} />
            <DataSection title="Unencrypted metadata" data={unencryptedData} />
          </div>
        ) : null}
        {listenerState === 'error' && errorMessage ? (
          <p className="emptyState">{errorMessage}</p>
        ) : null}
        {listenerState === 'idle' && !payload ? (
          <p className="emptyState">Submit a channel to begin listening for uploads.</p>
        ) : null}
      </section>
    </main>
  );
}

export default App;
