import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import CreateRoom from "./pages/CreateRoom";
import Room from "./pages/Room";
import Report from "./pages/Report";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<CreateRoom />} />
        <Route path="/room/:id" element={<Room />} />
        <Route path="/room/:id/report" element={<Report />} />
        <Route path="/meetings/:id/report" element={<Report />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
