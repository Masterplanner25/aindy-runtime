import { useAuth } from "@aindy/ui-kit";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";

export default function ExecutionConsole() {
  const { isAdmin } = useAuth();
  if (!isAdmin) return <AdminAccessRequired />;

  return (
    <div className="flex flex-col items-center justify-center min-h-[300px] gap-4">
      <div className="text-center">
        <h2 className="text-xl font-black text-zinc-100 tracking-tight mb-2">
          Execution Console
        </h2>
        <p className="text-sm text-zinc-500 max-w-sm">
          Execution analytics are not available in runtime-only mode. Access
          the full workspace to view TWR, KPI panels, and domain analytics.
        </p>
      </div>
    </div>
  );
}
