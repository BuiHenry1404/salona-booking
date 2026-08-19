import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { AdminDashboard } from "./screens/AdminDashboard";
import { ChatHistoryScreen } from "./screens/ChatHistoryScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { MyAppointmentsScreen } from "./screens/MyAppointmentsScreen";
import { PastChatScreen } from "./screens/PastChatScreen";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/dang-nhap" element={<LoginScreen />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <ChatScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/lich-su"
            element={
              <RequireAuth>
                <ChatHistoryScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/lich-su/:day"
            element={
              <RequireAuth>
                <PastChatScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/lich-cua-toi"
            element={
              <RequireAuth>
                <MyAppointmentsScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/chu-tiem"
            element={
              <RequireAuth role="admin">
                <AdminDashboard />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
