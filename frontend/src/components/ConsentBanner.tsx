import { Cookie } from "lucide-react";
import { useEffect, useState } from "react";
import { getConsent, setConsent } from "../lib/analytics";

/** Shown until the visitor chooses. Essential cookies (sign-in) are always on; analytics only with consent. */
export function ConsentBanner() {
  const [open, setOpen] = useState(() => getConsent() === null);
  useEffect(() => {
    const sync = () => setOpen(getConsent() === null);
    addEventListener("ib:consent", sync);
    return () => removeEventListener("ib:consent", sync);
  }, []);
  if (!open) return null;
  return (
    <div role="dialog" aria-label="Cookie preferences"
      className="fixed inset-x-4 bottom-4 z-50 mx-auto max-w-2xl rounded-2xl border border-line bg-surface p-5 text-ink shadow-[0_8px_30px_rgba(0,0,0,.25)]">
      <div className="flex gap-3">
        <Cookie size={20} className="mt-0.5 shrink-0 text-amber" />
        <div>
          <p className="font-semibold">Cookies</p>
          <p className="mt-1 text-[15px] text-graphite">
            We use essential cookies to keep you signed in. With your OK we also use an analytics cookie to see
            how InterviewBud is used, so we can improve it. It stays with us; nothing goes to third parties.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2"
              onClick={() => setConsent("all")}>Accept all</button>
            <button className="rounded-lg border border-line px-4 py-2 text-sm font-medium hover:bg-paper"
              onClick={() => setConsent("essential")}>Essential only</button>
          </div>
        </div>
      </div>
    </div>
  );
}
