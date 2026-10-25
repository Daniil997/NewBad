import { CompanyProfile } from "./CompanyProfile";
import type { SavedProfile } from "./realTypes";
import type { ClientProfile } from "./demoTypes";

export function DemoProfile({ profile, onBack, onSaved, onDirtyChange, onBusyChange }: {
  profile: ClientProfile; onBack: () => void; onSaved: (profile: SavedProfile) => void;
  onDirtyChange: (dirty: boolean) => void; onBusyChange: (busy: boolean) => void;
}) {
  return <CompanyProfile
    key={profile.client_id} mode="demo"
    apiBase={`/api/demo/clients/${encodeURIComponent(profile.client_id)}/profile`}
    onBack={onBack} onSaved={onSaved} onDirtyChange={onDirtyChange} onBusyChange={onBusyChange}
  />;
}
