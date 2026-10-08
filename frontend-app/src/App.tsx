import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getConfig, getMe, logout } from "@/api/platform";
import { isBackendConfigured, setAuthMode, setBearerToken, setUnauthorizedHandler } from "@/api/client";
import { isAuthError } from "@/api/errors";
import { AppProvider } from "@/hooks/useApp";
import { usePreferences } from "@/hooks/useTheme";
import { Layout } from "@/components/common/Layout";
import { ErrorState, Skeleton } from "@/components/common/StateViews";
import { SetupScreen } from "@/pages/SetupScreen";
import { LoginScreen } from "@/pages/LoginScreen";
import { UploadPage } from "@/pages/UploadPage";
import { BatchesPage, BatchProgressPage } from "@/pages/BatchPages";
import { ResultsPage } from "@/pages/ResultsPage";
import { SourceViewerPage } from "@/pages/SourceViewerPage";
import { CasesPage, CaseReviewPage } from "@/pages/CasePages";
import { ActionPage } from "@/pages/ActionPage";
import { AccessPage } from "@/pages/AccessPage";
import { ChatPage } from "@/pages/ChatPage";
import { ExportsPage } from "@/pages/ExportsPage";
import { AuditPage } from "@/pages/AuditPage";
import { MetricsPage } from "@/pages/MetricsPage";
import { AgentStatusPage } from "@/pages/AgentStatusPage";
import { SettingsPage } from "@/pages/SettingsPage";

export default function App() {
  usePreferences(); // applies saved theme/density classes
  const qc = useQueryClient();
  const [signedOut, setSignedOut] = useState(false);
  const configured = isBackendConfigured();

  useEffect(() => { setUnauthorizedHandler(() => setSignedOut(true)); return () => setUnauthorizedHandler(null); }, []);

  const config = useQuery({ queryKey: ["config"], queryFn: ({ signal }) => getConfig(signal), enabled: configured, staleTime: Infinity, retry: false });
  const me = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => getMe(signal), enabled: configured, retry: false });

  useEffect(() => { if (config.data) setAuthMode(config.data.auth.mode); }, [config.data]);

  if (!configured) return <SetupScreen />;
  if (config.isPending || me.isPending) return <div className="mx-auto mt-24 max-w-sm"><Skeleton lines={4} /></div>;

  const needsLogin = signedOut || isAuthError(config.error) || isAuthError(me.error);
  const signedIn = () => { setSignedOut(false); void qc.invalidateQueries(); };
  if (needsLogin) return <LoginScreen config={config.data ?? null} onSignedIn={signedIn} />;

  if (config.isError) return <div className="mx-auto mt-16 max-w-xl card card-pad"><ErrorState error={config.error} onRetry={() => void config.refetch()} /></div>;
  if (me.isError) return <div className="mx-auto mt-16 max-w-xl card card-pad"><ErrorState error={me.error} onRetry={() => void me.refetch()} /></div>;

  const signOut = () => {
    void logout().catch(() => undefined).finally(() => { setBearerToken(null); qc.clear(); setSignedOut(true); });
  };

  return (
    <AppProvider value={{ config: config.data, me: me.data, signOut }}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<UploadPage />} />
          <Route path="batches" element={<BatchesPage />} />
          <Route path="batches/:batchId" element={<BatchProgressPage />} />
          <Route path="batches/:batchId/results" element={<ResultsPage />} />
          <Route path="sources/:sourceId" element={<SourceViewerPage />} />
          <Route path="cases" element={<CasesPage />} />
          <Route path="cases/:caseId" element={<CaseReviewPage />} />
          <Route path="cases/:caseId/actions/:actionId" element={<ActionPage />} />
          <Route path="access" element={<AccessPage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="exports" element={<ExportsPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="metrics" element={<MetricsPage />} />
          <Route path="agents" element={<AgentStatusPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<p className="text-sm text-muted">This page does not exist.</p>} />
        </Route>
      </Routes>
    </AppProvider>
  );
}
