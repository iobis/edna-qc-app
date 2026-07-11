import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import LocationMiniMap from './LocationMiniMap';

function CoordinatePopover({ lon, lat }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const triggerRef = useRef(null);
  const panelRef = useRef(null);

  const updatePosition = () => {
    if (!triggerRef.current) {
      return;
    }
    const rect = triggerRef.current.getBoundingClientRect();
    const panelWidth = 280;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - panelWidth - 8));
    setPosition({ top: rect.bottom + 6, left });
  };

  const openPopover = () => {
    updatePosition();
    setOpen(true);
  };

  const closePopover = () => {
    setOpen(false);
  };

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        closePopover();
      }
    };

    const onPointerDown = (event) => {
      const target = event.target;
      if (
        triggerRef.current?.contains(target)
        || panelRef.current?.contains(target)
      ) {
        return;
      }
      closePopover();
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('mousedown', onPointerDown);
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);

    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('mousedown', onPointerDown);
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [open]);

  const label = `${lon}, ${lat}`;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="coord-popover-trigger"
        onClick={() => (open ? closePopover() : openPopover())}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        {label}
      </button>
      {open && createPortal(
        <div
          ref={panelRef}
          className="coord-popover-panel"
          role="dialog"
          aria-label={`Map at ${label}`}
          style={{ top: position.top, left: position.left }}
        >
          <LocationMiniMap lon={lon} lat={lat} />
        </div>,
        document.body
      )}
    </>
  );
}

export default CoordinatePopover;
