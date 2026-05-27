const form = document.getElementById("applicationForm");

const fields = {
  fullName: {
    input: document.getElementById("fullName"),
    error: document.getElementById("fullNameError"),
    validate: (value) => {
      if (!value.trim()) return "El nombre es obligatorio.";
      if (value.trim().length < 3) return "El nombre debe tener al menos 3 caracteres.";
      return "";
    },
  },
  email: {
    input: document.getElementById("email"),
    error: document.getElementById("emailError"),
    validate: (value) => {
      if (!value.trim()) return "El correo es obligatorio.";
      const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!regex.test(value.trim())) return "Ingresa un correo válido.";
      return "";
    },
  },
  phone: {
    input: document.getElementById("phone"),
    error: document.getElementById("phoneError"),
    validate: (value) => {
      if (!value.trim()) return "El teléfono es obligatorio.";
      const digits = value.replace(/\D/g, "");
      if (digits.length < 8) return "El teléfono debe tener al menos 8 dígitos.";
      return "";
    },
  },
  role: {
    input: document.getElementById("role"),
    error: document.getElementById("roleError"),
    validate: (value) => {
      if (!value) return "Selecciona un cargo.";
      return "";
    },
  },
  message: {
    input: document.getElementById("message"),
    error: document.getElementById("messageError"),
    validate: (value) => {
      if (!value.trim()) return "El mensaje es obligatorio.";
      if (value.trim().length < 20) return "El mensaje debe tener al menos 20 caracteres.";
      return "";
    },
  },
};

const successMessage = document.getElementById("formSuccess");

function setFieldState(field, message) {
  field.error.textContent = message;
  field.input.classList.remove("error-input", "success-input");

  if (message) {
    field.input.classList.add("error-input");
  } else {
    field.input.classList.add("success-input");
  }
}

function validateField(fieldName) {
  const field = fields[fieldName];
  const message = field.validate(field.input.value);
  setFieldState(field, message);
  return !message;
}

Object.keys(fields).forEach((key) => {
  fields[key].input.addEventListener("blur", () => validateField(key));
  fields[key].input.addEventListener("input", () => {
    if (fields[key].error.textContent) validateField(key);
  });
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  successMessage.textContent = "";

  const allValid = Object.keys(fields)
    .map((key) => validateField(key))
    .every(Boolean);

  if (!allValid) return;

  successMessage.textContent = "Formulario enviado correctamente.";
  form.reset();

  Object.values(fields).forEach((field) => {
    field.error.textContent = "";
    field.input.classList.remove("error-input", "success-input");
  });
});
