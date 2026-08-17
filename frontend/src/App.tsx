import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { LoginScreen } from "./screens/LoginScreen";

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
                <p style={{ padding: "var(--s4)" }}>Màn hình chat — task 4</p>
              </RequireAuth>
            }
          />
          <Route
            path="/chu-tiem"
            element={
              <RequireAuth role="admin">
                <div>Màn hình chủ tiệm — task 6</div>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
