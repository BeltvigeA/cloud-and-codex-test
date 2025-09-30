import React, { useState } from 'react';

const statusLabels = {
  idle: { label: 'Idle', className: 'statusIdle' },
  listening: { label: 'Listening…', className: 'statusListening' },
  received: { label: 'Data received', className: 'statusReceived' },
  error: { label: 'No data', className: 'statusError' }
};

function ChannelListener({ onChannelSubmit, listenerState, errorMessage }) {
  const [channelInput, setChannelInput] = useState('user-123');

  const handleSubmit = (event) => {
    event.preventDefault();
    const sanitizedChannel = channelInput.trim();
    if (sanitizedChannel) {
      onChannelSubmit(sanitizedChannel);
    }
  };

  const status = statusLabels[listenerState] ?? statusLabels.idle;

  return (
    <section className="listenerCard" aria-label="Channel listener">
      <span className={`statusBadge ${status.className}`}>{status.label}</span>
      <h2>Listen to a channel</h2>
      <form className="listenerForm" onSubmit={handleSubmit}>
        <label htmlFor="channelInput">
          Channel ID
          <input
            id="channelInput"
            className="listenerInput"
            placeholder="user-123"
            value={channelInput}
            onChange={(event) => setChannelInput(event.target.value)}
            aria-label="Channel identifier"
          />
        </label>
        <button className="listenerButton" type="submit">
          Start listening
        </button>
      </form>
      {errorMessage ? <div className="errorMessage">{errorMessage}</div> : null}
    </section>
  );
}

export default ChannelListener;
