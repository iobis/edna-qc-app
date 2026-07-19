import React, { useMemo, useState } from 'react';
import { interpolateMagma } from 'd3-scale-chromatic';

const WIDTH = 720;
const HEIGHT = 280;
const MARGIN = { top: 16, right: 20, bottom: 40, left: 48 };
const INNER_W = WIDTH - MARGIN.left - MARGIN.right;
const INNER_H = HEIGHT - MARGIN.top - MARGIN.bottom;

function scaleLinear(value, size) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  return v * size;
}

function combinedScore(density, suitability) {
  return (Number(density) + Number(suitability)) / 2;
}

function scoreColor(score) {
  const t = Math.max(0, Math.min(1, score));
  // Use only the middle half of the scale (avoid near-black / washed ends)
  return interpolateMagma(0.25 + t * 0.5);
}

export default function ScoreDotPlot({ occurrences }) {
  const [hover, setHover] = useState(null);

  const points = useMemo(() => {
    if (!occurrences?.length) return [];
    return occurrences
      .map((occ, index) => {
        const density = occ.density;
        const suitability = occ.suitability;
        if (density == null || suitability == null) return null;
        const score = combinedScore(density, suitability);
        return {
          key: `${occ.aphiaid ?? 'na'}|${occ.decimalLongitude ?? 'na'}|${occ.decimalLatitude ?? 'na'}|${index}`,
          name: occ.scientificName || String(occ.aphiaid || 'Unknown'),
          density,
          suitability,
          score,
          color: scoreColor(score),
        };
      })
      .filter(Boolean);
  }, [occurrences]);

  if (points.length === 0) {
    return null;
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="score-dotplot">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Density versus suitability scatter plot"
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {ticks.map((t) => {
            const x = scaleLinear(t, INNER_W);
            const y = INNER_H - scaleLinear(t, INNER_H);
            return (
              <g key={t}>
                <line
                  className="score-dotplot-grid"
                  x1={x}
                  y1={0}
                  x2={x}
                  y2={INNER_H}
                />
                <line
                  className="score-dotplot-grid"
                  x1={0}
                  y1={y}
                  x2={INNER_W}
                  y2={y}
                />
              </g>
            );
          })}

          <line className="score-dotplot-axis" x1={0} y1={INNER_H} x2={INNER_W} y2={INNER_H} />
          <line className="score-dotplot-axis" x1={0} y1={0} x2={0} y2={INNER_H} />

          {ticks.map((t) => {
            const x = scaleLinear(t, INNER_W);
            const y = INNER_H - scaleLinear(t, INNER_H);
            return (
              <g key={`tick-${t}`}>
                <text className="score-dotplot-tick" x={x} y={INNER_H + 16} textAnchor="middle">
                  {t.toFixed(2)}
                </text>
                <text className="score-dotplot-tick" x={-10} y={y + 4} textAnchor="end">
                  {t.toFixed(2)}
                </text>
              </g>
            );
          })}

          <text
            className="score-dotplot-label"
            x={INNER_W / 2}
            y={INNER_H + 34}
            textAnchor="middle"
          >
            Density
          </text>
          <text
            className="score-dotplot-label"
            transform={`translate(-36 ${INNER_H / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            Suitability
          </text>

          {points.map((p) => {
            const cx = scaleLinear(p.density, INNER_W);
            const cy = INNER_H - scaleLinear(p.suitability, INNER_H);
            const active = hover?.key === p.key;
            return (
              <circle
                key={p.key}
                className={`score-dotplot-point${active ? ' is-active' : ''}`}
                cx={cx}
                cy={cy}
                r={active ? 4 : 2.75}
                fill={p.color}
                stroke={p.color}
                onMouseEnter={(event) => {
                  const container = event.currentTarget.closest('.score-dotplot');
                  const rect = container.getBoundingClientRect();
                  setHover({
                    key: p.key,
                    name: p.name,
                    density: p.density,
                    suitability: p.suitability,
                    score: p.score,
                    x: event.clientX - rect.left,
                    y: event.clientY - rect.top,
                  });
                }}
                onMouseMove={(event) => {
                  const container = event.currentTarget.closest('.score-dotplot');
                  const rect = container.getBoundingClientRect();
                  setHover((prev) =>
                    prev && prev.key === p.key
                      ? {
                          ...prev,
                          x: event.clientX - rect.left,
                          y: event.clientY - rect.top,
                        }
                      : prev
                  );
                }}
                onMouseLeave={() => setHover(null)}
              >
                <title>{p.name}</title>
              </circle>
            );
          })}
        </g>
      </svg>

      {hover && (
        <div
          className="score-dotplot-tooltip"
          style={{ left: hover.x + 12, top: hover.y + 12 }}
        >
          <div className="score-dotplot-tooltip-name">{hover.name}</div>
          <div className="score-dotplot-tooltip-meta">
            Density {Number(hover.density).toFixed(3)} · Suitability{' '}
            {Number(hover.suitability).toFixed(3)}
          </div>
        </div>
      )}
    </div>
  );
}
