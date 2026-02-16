import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AdminAuthProvider, ClientAuthProvider } from "@/contexts/AuthContext";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";

// Existing pages
import Index from "./pages/Index";
import Scraper from "./pages/Scraper";
import TenderDetail from "./pages/TenderDetail";
import NotFound from "./pages/NotFound";

// Admin pages
import AdminLogin from "./pages/admin/AdminLogin";
import AdminDashboard from "./pages/admin/AdminDashboard";
import AdminAccounts from "./pages/admin/AdminAccounts";
import AdminScraper from "./pages/admin/AdminScraper";
import AdminStats from "./pages/admin/AdminStats";

// Client pages
import ClientLogin from "./pages/client/ClientLogin";
import ClientTenders from "./pages/client/ClientTenders";
import ClientTenderDetail from "./pages/client/ClientTenderDetail";
import ClientProfile from "./pages/client/ClientProfile";

const queryClient = new QueryClient();

// Admin routes wrapped with AdminAuthProvider
function AdminRoutes() {
  return (
    <AdminAuthProvider>
      <Routes>
        <Route path="login" element={<AdminLogin />} />
        <Route path="dashboard" element={
          <ProtectedRoute requiredType="admin" loginPath="/admin/login">
            <AdminDashboard />
          </ProtectedRoute>
        } />
        <Route path="accounts" element={
          <ProtectedRoute requiredType="admin" loginPath="/admin/login">
            <AdminAccounts />
          </ProtectedRoute>
        } />
        <Route path="scraper" element={
          <ProtectedRoute requiredType="admin" loginPath="/admin/login">
            <AdminScraper />
          </ProtectedRoute>
        } />
        <Route path="stats" element={
          <ProtectedRoute requiredType="admin" loginPath="/admin/login">
            <AdminStats />
          </ProtectedRoute>
        } />
        <Route path="" element={<Navigate to="/admin/dashboard" replace />} />
      </Routes>
    </AdminAuthProvider>
  );
}

// Client routes wrapped with ClientAuthProvider
function ClientRoutes() {
  return (
    <ClientAuthProvider>
      <Routes>
        <Route path="login" element={<ClientLogin />} />
        <Route path="tenders" element={
          <ProtectedRoute requiredType="client" loginPath="/client/login">
            <ClientTenders />
          </ProtectedRoute>
        } />
        <Route path="tender/:id" element={
          <ProtectedRoute requiredType="client" loginPath="/client/login">
            <ClientTenderDetail />
          </ProtectedRoute>
        } />
        <Route path="profile" element={
          <ProtectedRoute requiredType="client" loginPath="/client/login">
            <ClientProfile />
          </ProtectedRoute>
        } />
        <Route path="" element={<Navigate to="/client/tenders" replace />} />
      </Routes>
    </ClientAuthProvider>
  );
}

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          {/* Legacy/dev routes */}
          <Route path="/" element={<Index />} />
          <Route path="/scraper" element={<Scraper />} />
          <Route path="/tender/:id" element={<TenderDetail />} />
          
          {/* Admin portal */}
          <Route path="/admin/*" element={<AdminRoutes />} />
          
          {/* Client portal */}
          <Route path="/client/*" element={<ClientRoutes />} />
          
          {/* Catch-all */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
