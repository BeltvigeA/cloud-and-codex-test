import React from 'react';

function DataSection({ title, data }) {
  const entries = Object.entries(data ?? {});
  const hasEntries = entries.length > 0;

  return (
    <section className="dataSection">
      <h3>{title}</h3>
      {hasEntries ? (
        <dl className="dataList">
          {entries.map(([key, value]) => (
            <div key={key} className="dataRow">
              <dt className="dataLabel">{key}</dt>
              <dd className="dataValue">{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="emptyState">No data provided.</p>
      )}
    </section>
  );
}

export default DataSection;
