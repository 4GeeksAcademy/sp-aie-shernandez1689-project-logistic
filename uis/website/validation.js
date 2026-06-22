const form = document.getElementById("applicationForm");
const successMessage = document.getElementById("formSuccess");
const volumeWarning = document.getElementById("volumeWarning");
const commentsCounter = document.getElementById("commentsCounter");
let keepSuccessOnReset = false;

const inputs = {
  companyName: document.getElementById("companyName"),
  contactPerson: document.getElementById("contactPerson"),
  corporateEmail: document.getElementById("corporateEmail"),
  phone: document.getElementById("phone"),
  companyWebsite: document.getElementById("companyWebsite"),
  operatingCountry: document.getElementById("operatingCountry"),
  productType: document.getElementById("productType"),
  monthlyVolume: document.getElementById("monthlyVolume"),
  comments: document.getElementById("comments"),
  privacyPolicy: document.getElementById("privacyPolicy"),
};

const errors = {
  companyName: document.getElementById("companyNameError"),
  contactPerson: document.getElementById("contactPersonError"),
  corporateEmail: document.getElementById("corporateEmailError"),
  phone: document.getElementById("phoneError"),
  companyWebsite: document.getElementById("companyWebsiteError"),
  operatingCountry: document.getElementById("operatingCountryError"),
  productType: document.getElementById("productTypeError"),
  monthlyVolume: document.getElementById("monthlyVolumeError"),
  servicesInterest: document.getElementById("servicesInterestError"),
  current3pl: document.getElementById("current3plError"),
  comments: document.getElementById("commentsError"),
  privacyPolicy: document.getElementById("privacyPolicyError"),
};

const servicesCheckboxes = Array.from(document.querySelectorAll('input[name="servicesInterest"]'));
const current3plRadios = Array.from(document.querySelectorAll('input[name="current3pl"]'));

function setInputState(input, message) {
  if (!input) return;
  input.classList.remove("error-input", "success-input");
  if (message) {
    input.classList.add("error-input");
  } else if (input.type !== "checkbox" && input.type !== "radio") {
    input.classList.add("success-input");
  }
}

function setError(fieldName, message) {
  errors[fieldName].textContent = message;
}

function getCheckedServicesCount() {
  return servicesCheckboxes.filter((checkbox) => checkbox.checked).length;
}

function getCurrent3plValue() {
  const checked = current3plRadios.find((radio) => radio.checked);
  return checked ? checked.value : "";
}

function updateVolumeWarning() {
  const hasLowVolume = inputs.monthlyVolume.value === "0-100";
  const hasProductType = Boolean(inputs.productType.value);

  volumeWarning.textContent =
    hasLowVolume && hasProductType
      ? "For volumes under 100 monthly shipments, our services might not be the most efficient solution. Are you sure you want to continue?"
      : "";
}

function updateCommentsCounter() {
  const remaining = 500 - inputs.comments.value.length;
  commentsCounter.textContent = `${remaining} remaining`;
}

function validateCompanyName() {
  const value = inputs.companyName.value.trim();
  let message = "";

  if (!value) {
    message = "Company name is required";
  } else if (value.length < 2) {
    message = "Company name must have at least 2 characters";
  }

  setError("companyName", message);
  setInputState(inputs.companyName, message);
  return !message;
}

function validateContactPerson() {
  const words = inputs.contactPerson.value.trim().split(/\s+/).filter(Boolean);
  const rawValue = inputs.contactPerson.value.trim();
  let message = "";

  if (!rawValue) {
    message = "Contact person is required";
  } else if (words.length < 2) {
    message = "Enter first and last name of contact";
  }

  setError("contactPerson", message);
  setInputState(inputs.contactPerson, message);
  return !message;
}

function validateCorporateEmail() {
  const value = inputs.corporateEmail.value.trim();
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  let message = "";

  if (!value) {
    message = "Corporate email is required";
  } else if (!regex.test(value)) {
    message = "Enter a valid corporate email (example: <name@company.com>)";
  }

  setError("corporateEmail", message);
  setInputState(inputs.corporateEmail, message);
  return !message;
}

function validatePhone() {
  const value = inputs.phone.value.trim();
  const regex = /^\+\d{1,3}[\s\d-]{5,}$/;
  let message = "";

  if (!value) {
    message = "Phone is required";
  } else if (!regex.test(value)) {
    message = "Phone must include country code (example: +1 213 555 0147)";
  }

  setError("phone", message);
  setInputState(inputs.phone, message);
  return !message;
}

function validateCompanyWebsite() {
  const value = inputs.companyWebsite.value.trim();
  let message = "";

  if (value) {
    try {
      const url = new URL(value);
      if (!(url.protocol === "http:" || url.protocol === "https:")) {
        message = "If you include website, it must be a valid URL";
      }
    } catch {
      message = "If you include website, it must be a valid URL";
    }
  }

  setError("companyWebsite", message);
  setInputState(inputs.companyWebsite, message);
  return !message;
}

function validateOperatingCountry() {
  const message = inputs.operatingCountry.value ? "" : "Main operating country is required";
  setError("operatingCountry", message);
  setInputState(inputs.operatingCountry, message);
  return !message;
}

function validateProductType() {
  const message = inputs.productType.value ? "" : "Product type is required";
  setError("productType", message);
  setInputState(inputs.productType, message);
  updateVolumeWarning();
  return !message;
}

function validateMonthlyVolume() {
  const message = inputs.monthlyVolume.value ? "" : "Estimated monthly volume is required";
  setError("monthlyVolume", message);
  setInputState(inputs.monthlyVolume, message);
  updateVolumeWarning();
  return !message;
}

function validateServicesInterest() {
  const message = getCheckedServicesCount() > 0 ? "" : "At least one service of interest is required";
  setError("servicesInterest", message);
  return !message;
}

function validateCurrent3pl() {
  const message = getCurrent3plValue() ? "" : "Current 3PL selection is required";
  setError("current3pl", message);
  return !message;
}

function validateComments() {
  const remaining = 500 - inputs.comments.value.length;
  const message = remaining < 0 ? `Comments cannot exceed 500 characters (${remaining} remaining)` : "";
  setError("comments", message);
  setInputState(inputs.comments, message);
  updateCommentsCounter();
  return !message;
}

function validatePrivacyPolicy() {
  const message = inputs.privacyPolicy.checked ? "" : "Privacy policy acceptance is required";
  setError("privacyPolicy", message);
  return !message;
}

function focusFirstInvalidField() {
  const orderedCandidates = [
    inputs.companyName,
    inputs.contactPerson,
    inputs.corporateEmail,
    inputs.phone,
    inputs.companyWebsite,
    inputs.operatingCountry,
    inputs.productType,
    inputs.monthlyVolume,
    ...servicesCheckboxes,
    ...current3plRadios,
    inputs.comments,
    inputs.privacyPolicy,
  ];

  const firstInvalid = orderedCandidates.find((input) => input.classList.contains("error-input"))
    || servicesCheckboxes.find(() => Boolean(errors.servicesInterest.textContent))
    || current3plRadios.find(() => Boolean(errors.current3pl.textContent))
    || (errors.privacyPolicy.textContent ? inputs.privacyPolicy : null);

  if (firstInvalid) {
    firstInvalid.focus();
  }
}

function resetValidationState() {
  Object.values(errors).forEach((errorNode) => {
    errorNode.textContent = "";
  });

  Object.values(inputs).forEach((input) => {
    input.classList.remove("error-input", "success-input");
  });

  volumeWarning.textContent = "";
  commentsCounter.textContent = "500 remaining";
}

function attachRealtimeValidation(input, validateFn, eventName = "input") {
  input.addEventListener(eventName, () => {
    successMessage.textContent = "";
    validateFn();
  });

  input.addEventListener("blur", validateFn);
}

attachRealtimeValidation(inputs.companyName, validateCompanyName);
attachRealtimeValidation(inputs.contactPerson, validateContactPerson);
attachRealtimeValidation(inputs.corporateEmail, validateCorporateEmail);
attachRealtimeValidation(inputs.phone, validatePhone);
attachRealtimeValidation(inputs.companyWebsite, validateCompanyWebsite);
attachRealtimeValidation(inputs.comments, validateComments);

attachRealtimeValidation(inputs.operatingCountry, validateOperatingCountry, "change");
attachRealtimeValidation(inputs.productType, validateProductType, "change");
attachRealtimeValidation(inputs.monthlyVolume, validateMonthlyVolume, "change");
attachRealtimeValidation(inputs.privacyPolicy, validatePrivacyPolicy, "change");

servicesCheckboxes.forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    successMessage.textContent = "";
    validateServicesInterest();
  });
});

current3plRadios.forEach((radio) => {
  radio.addEventListener("change", () => {
    successMessage.textContent = "";
    validateCurrent3pl();
  });
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  successMessage.textContent = "";

  const allValid = [
    validateCompanyName(),
    validateContactPerson(),
    validateCorporateEmail(),
    validatePhone(),
    validateCompanyWebsite(),
    validateOperatingCountry(),
    validateProductType(),
    validateMonthlyVolume(),
    validateServicesInterest(),
    validateCurrent3pl(),
    validateComments(),
    validatePrivacyPolicy(),
  ].every(Boolean);

  if (!allValid) {
    focusFirstInvalidField();
    return;
  }

  successMessage.textContent =
    "Thank you for your interest in TrackFlow! We have received your request. Our commercial team will review your information and contact you within the next 24-48 hours to schedule a call and learn about your logistics needs in detail. If you have any urgent inquiry, write to us directly at <comercial@trackflow.com>";

  keepSuccessOnReset = true;
  form.reset();
  resetValidationState();
});

form.addEventListener("reset", () => {
  if (!keepSuccessOnReset) {
    successMessage.textContent = "";
  }

  keepSuccessOnReset = false;
  // Wait a tick so native reset updates values before restoring UI state.
  setTimeout(() => {
    resetValidationState();
  }, 0);
});
