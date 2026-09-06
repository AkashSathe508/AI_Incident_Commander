import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import CreateRoom from "./pages/CreateRoom";
import Room from "./pages/Room";
import Report from "./pages/Report";
import Dashboard from "./pages/Dashboard";
import MeetingHistory from "./pages/MeetingHistory";
import MeetingDetail from "./pages/MeetingDetail";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Main dashboard — command center */}
        <Route path="/" element={<Dashboard />} />
        {/* Create new incident room */}
        <Route path="/new" element={<CreateRoom />} />
        {/* Meeting history list */}
        <Route path="/meetings" element={<MeetingHistory />} />
        {/* Meeting detail view */}
        <Route path="/meetings/:id" element={<MeetingDetail />} />
        {/* Live incident room */}
        <Route path="/room/:id" element={<Room />} />
        {/* Post-meeting report */}
        <Route path="/room/:id/report" element={<Report />} />
        <Route path="/meetings/:id/report" element={<Report />} />
        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
