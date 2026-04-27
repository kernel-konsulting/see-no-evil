import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Login from "@/pages/Login";
import { AuthProvider } from "@/lib/auth";
import * as api from "@/lib/api";

vi.mock("@/lib/api");

function renderLogin() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Login page", () => {
  it("renders username and password fields", () => {
    renderLogin();
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("calls login API and navigates on success", async () => {
    vi.mocked(api.login).mockResolvedValue({ email: "admin@example.com" });
    renderLogin();
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(api.login).toHaveBeenCalledWith("admin", "secret"));
  });

  it("shows error toast on failed login", async () => {
    vi.mocked(api.login).mockRejectedValue(new Error("401"));
    renderLogin();
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(screen.getByText(/login failed/i)).toBeInTheDocument());
  });
});
