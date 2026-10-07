const TRACKFLOW_API_URL = (window.TRACKFLOW_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const TRACKFLOW_TOKEN_KEY = "trackflow_access_token";

function setElementText(id, text) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = text || "";
  }
}

function setFieldError(fieldName, message) {
  setElementText(`${fieldName}Error`, message);
}

function clearFieldErrors(fieldNames) {
  fieldNames.forEach((fieldName) => {
    setFieldError(fieldName, "");
  });
}

function saveToken(token) {
  localStorage.setItem(TRACKFLOW_TOKEN_KEY, token);
}

function getToken() {
  return localStorage.getItem(TRACKFLOW_TOKEN_KEY);
}

function clearToken() {
  localStorage.removeItem(TRACKFLOW_TOKEN_KEY);
}

function redirectToLogin(loginPath) {
  clearToken();
  window.location.href = loginPath;
}

function normalizeErrorPayload(payload, fallbackMessage) {
  const result = {
    message: fallbackMessage,
    fieldErrors: {},
  };

  if (!payload || payload.detail == null) {
    return result;
  }

  if (typeof payload.detail === "string") {
    result.message = payload.detail;
    return result;
  }

  if (Array.isArray(payload.detail)) {
    payload.detail.forEach((item) => {
      const location = Array.isArray(item.loc) ? item.loc : [];
      const fieldName = location[1];
      if (typeof fieldName === "string") {
        result.fieldErrors[fieldName] = item.msg || "Invalid value";
      }
    });

    if (Object.keys(result.fieldErrors).length > 0) {
      result.message = "Please review the highlighted fields.";
    }
  }

  return result;
}

async function requestJson(path, options = {}) {
  const response = await fetch(`${TRACKFLOW_API_URL}${path}`, options);
  let payload = null;

  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  return { response, payload };
}

async function requestProtectedJson(path, options = {}, loginPath) {
  const token = getToken();

  if (!token) {
    redirectToLogin(loginPath);
    return { response: null, payload: null, redirected: true };
  }

  const mergedHeaders = {
    ...(options.headers || {}),
    Authorization: `Bearer ${token}`,
  };

  const { response, payload } = await requestJson(path, {
    ...options,
    headers: mergedHeaders,
  });

  if (response.status === 401) {
    redirectToLogin(loginPath);
    return { response, payload, redirected: true };
  }

  return { response, payload, redirected: false };
}

async function performLogin(email, password) {
  const { response, payload } = await requestJson("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!response.ok) {
    throw normalizeErrorPayload(payload, "No se pudo iniciar sesión.");
  }

  return payload;
}

async function performRegister(userPayload) {
  const { response, payload } = await requestJson("/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(userPayload),
  });

  if (!response.ok) {
    throw normalizeErrorPayload(payload, "No se pudo crear la cuenta.");
  }

  return payload;
}

async function fetchAuthMe(loginPath) {
  const { response, payload, redirected } = await requestProtectedJson("/auth/me", {}, loginPath);

  if (redirected || !response) {
    throw new Error("Sesión inválida");
  }

  if (!response.ok) {
    throw normalizeErrorPayload(payload, "No se pudo validar la sesión.");
  }

  return payload;
}

async function updateMyProfile(profilePayload, loginPath) {
  const { response, payload, redirected } = await requestProtectedJson("/profiles/me", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(profilePayload),
  }, loginPath);

  if (redirected || !response) {
    throw new Error("Sesión inválida");
  }

  if (!response.ok) {
    throw normalizeErrorPayload(payload, "No se pudo actualizar el perfil.");
  }

  return payload;
}

async function requireAuthenticatedUser(loginPath) {
  try {
    const me = await fetchAuthMe(loginPath);
    const token = getToken();
    if (!token) {
      return null;
    }
    return { token, me };
  } catch {
    return null;
  }
}

function bindLoginView() {
  const form = document.getElementById("loginForm");
  if (!form) return;

  const submitButton = form.querySelector('button[type="submit"]');
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setElementText("loginError", "");
    clearFieldErrors(["email", "password"]);

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Ingresando...";
    }

    try {
      const result = await performLogin(emailInput.value.trim(), passwordInput.value);
      saveToken(result.access_token);
      window.location.href = "../app/index.html";
    } catch (errorData) {
      if (errorData.fieldErrors) {
        Object.entries(errorData.fieldErrors).forEach(([fieldName, message]) => {
          setFieldError(fieldName, message);
        });
      }
      setElementText("loginError", errorData.message || "No se pudo iniciar sesión.");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "Iniciar sesión";
      }
    }
  });
}

function bindRegisterView() {
  const form = document.getElementById("registerForm");
  if (!form) return;

  const submitButton = form.querySelector('button[type="submit"]');
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const nameInput = document.getElementById("name");
  const phoneInput = document.getElementById("phone");
  const addressInput = document.getElementById("address");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setElementText("registerError", "");
    clearFieldErrors(["email", "password", "name", "phone", "address"]);

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Creando cuenta...";
    }

    const email = emailInput.value.trim();
    const password = passwordInput.value;

    try {
      await performRegister({
        email,
        password,
        name: nameInput.value.trim() || null,
        phone: phoneInput.value.trim() || null,
        address: addressInput.value.trim() || null,
      });

      const loginResult = await performLogin(email, password);
      saveToken(loginResult.access_token);
      window.location.href = "../app/index.html";
    } catch (errorData) {
      if (errorData.fieldErrors) {
        Object.entries(errorData.fieldErrors).forEach(([fieldName, message]) => {
          setFieldError(fieldName, message);
        });
      }
      setElementText("registerError", errorData.message || "No se pudo completar el registro.");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "Crear cuenta";
      }
    }
  });
}

async function bindAppView() {
  const appRoot = document.getElementById("authApp");
  if (!appRoot) return;

  const logoutButton = document.getElementById("logoutButton");
  const auth = await requireAuthenticatedUser("../login/");
  if (!auth) {
    return;
  }

  const me = auth.me;
  setElementText("userEmail", me.email || "-");
  setElementText("userRole", me.role || "-");
  setElementText("profileName", me.profile?.name || "No definido");
  setElementText("profilePhone", me.profile?.phone || "No definido");
  setElementText("profileAddress", me.profile?.address || "No definido");

  if (logoutButton) {
    logoutButton.addEventListener("click", () => {
      clearToken();
      window.location.href = "../login/";
    });
  }
}

async function bindAccountProfileView() {
  const form = document.getElementById("accountProfileForm");
  if (!form) return;

  const logoutButton = document.getElementById("accountLogoutButton");
  const auth = await requireAuthenticatedUser("../../login/");
  if (!auth) {
    return;
  }

  const submitButton = form.querySelector('button[type="submit"]');
  const emailInput = document.getElementById("accountEmail");
  const nameInput = document.getElementById("accountName");
  const phoneInput = document.getElementById("accountPhone");
  const addressInput = document.getElementById("accountAddress");

  const me = auth.me;
  emailInput.value = me.email || "";
  nameInput.value = me.profile?.name || "";
  phoneInput.value = me.profile?.phone || "";
  addressInput.value = me.profile?.address || "";

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setElementText("accountProfileSuccess", "");
    setElementText("accountProfileError", "");
    clearFieldErrors(["accountName", "accountPhone", "accountAddress"]);

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Guardando...";
    }

    try {
      const updated = await updateMyProfile({
        name: nameInput.value.trim() || null,
        phone: phoneInput.value.trim() || null,
        address: addressInput.value.trim() || null,
      }, "../../login/");

      nameInput.value = updated.name || "";
      phoneInput.value = updated.phone || "";
      addressInput.value = updated.address || "";
      setElementText("accountProfileSuccess", "Perfil actualizado correctamente.");
    } catch (errorData) {
      if (errorData.fieldErrors) {
        const fieldMap = {
          name: "accountName",
          phone: "accountPhone",
          address: "accountAddress",
        };

        Object.entries(errorData.fieldErrors).forEach(([fieldName, message]) => {
          const mappedField = fieldMap[fieldName];
          if (mappedField) {
            setFieldError(mappedField, message);
          }
        });
      }

      setElementText("accountProfileError", errorData.message || "No se pudo actualizar el perfil.");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "Guardar cambios";
      }
    }
  });

  if (logoutButton) {
    logoutButton.addEventListener("click", () => {
      clearToken();
      window.location.href = "../../login/";
    });
  }
}

bindLoginView();
bindRegisterView();
bindAppView();
bindAccountProfileView();
