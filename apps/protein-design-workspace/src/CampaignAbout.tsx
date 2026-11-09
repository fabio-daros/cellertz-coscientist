import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

export const CAMPAIGN_ABOUT_STORAGE_KEY = "cellertz.campaign-about-seen";

function seenThisSession(): boolean {
  try {
    return window.sessionStorage.getItem(CAMPAIGN_ABOUT_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function rememberThisSession(): void {
  try {
    window.sessionStorage.setItem(CAMPAIGN_ABOUT_STORAGE_KEY, "1");
  } catch {
    /* The explanation still closes. A full session store is not required. */
  }
}

function focusable(dialog: HTMLElement): HTMLElement[] {
  return [...dialog.querySelectorAll<HTMLElement>("button, [href], input, select, textarea")].filter(
    (element) => !element.hasAttribute("disabled"),
  );
}

export function CampaignAbout({
  open,
  onClose,
  returnFocus,
}: {
  open: boolean;
  onClose: () => void;
  returnFocus: HTMLElement | null;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const returnFocusRef = useRef(returnFocus);
  const restoreRef = useRef<HTMLElement | null>(null);
  onCloseRef.current = onClose;
  returnFocusRef.current = returnFocus;

  useEffect(() => {
    if (!open) {
      const back = restoreRef.current;
      restoreRef.current = null;
      if (back && document.contains(back)) {
        back.focus();
      }
      return;
    }
    rememberThisSession();
    const trigger = returnFocusRef.current;
    const active = document.activeElement;
    restoreRef.current =
      trigger ?? (active instanceof HTMLElement && active !== document.body ? active : null);
    const page = document.querySelector(".page");
    if (page instanceof HTMLElement && "inert" in page) {
      page.inert = true;
    }
    dialogRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      const dialog = dialogRef.current;
      if (!dialog) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const items = focusable(dialog);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      if (!dialog.contains(current) || current === dialog) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
        return;
      }
      if (event.shiftKey && current === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && current === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (page instanceof HTMLElement && "inert" in page) {
        page.inert = false;
      }
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return createPortal(
    <div className="about-backdrop">
      <div
        ref={dialogRef}
        className="about-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="about-dialog-header">
          <h2 id={titleId}>About this workspace</h2>
          <button type="button" className="about-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="about-dialog-body">
          <p>This page organizes research campaigns around a scientific objective.</p>
          <ul>
            <li>
              A campaign records a name, a scientific objective written by the researcher, and
              optional notes.
            </li>
            <li>Saving a campaign does not create a design request, a plan, or an execution.</li>
            <li>
              Available demonstration: open the recorded PDL1 binder workflow and its saved results
              when one is configured for this installation.
            </li>
            <li>Reference configurations: consult the CD22 and TACI examples.</li>
            <li>Planned modules: see capabilities planned for future development.</li>
          </ul>
          <p>Opening this page does not start a computation.</p>
        </div>
        <div className="about-dialog-actions">
          <button type="button" className="about-primary" onClick={onClose}>
            Got it
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

let automaticAboutOpen = false;

export function resetAutomaticCampaignAbout(): void {
  automaticAboutOpen = false;
}

function initialAboutOpen(): boolean {
  if (automaticAboutOpen) {
    return true;
  }
  if (seenThisSession()) {
    return false;
  }
  rememberThisSession();
  automaticAboutOpen = true;
  return true;
}

export function useCampaignAbout() {
  const [open, setOpen] = useState(initialAboutOpen);
  const returnFocus = useRef<HTMLElement | null>(null);

  function show(trigger: HTMLElement | null) {
    returnFocus.current = trigger;
    setOpen(true);
  }

  function close() {
    automaticAboutOpen = false;
    rememberThisSession();
    setOpen(false);
  }

  return { open, show, close, returnFocus: returnFocus.current };
}
