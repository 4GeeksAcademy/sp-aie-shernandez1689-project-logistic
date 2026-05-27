const form = document.getElementById("applicationForm");
const successMessage = document.getElementById("formSuccess");
const volumeWarning = document.getElementById("volumeWarning");
const commentsCounter = document.getElementById("commentsCounter");

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
  const message = value.length >= 2 ? "" : "Company name must have at least 2 characters";
  setError("companyName", message);
  setInputState(inputs.companyName, message);
  return !message;
}

function validateContactPerson() {
  const words = inputs.contactPerson.value.trim().split(/\s+/).filter(Boolean);
  const message = words.length >= 2 ? "" : "Enter first and last name of contact";
  setError("contactPerson", message);
  setInputState(inputs.contactPerson, message);
  return !message;
}

function validateCorporateEmail() {
  const value = inputs.corporateEmail.value.trim();
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const message = regex.test(value)
    ? ""
    : "Enter a valid corporate email (example: <name@company.com>)";
  setError("corporateEmail", message);
  setInputState(inputs.corporateEmail, message);
  return !message;
}

function validatePhone() {
  const value = inputs.phone.value.trim();
  const regex = /^\+\d{1,3}[\s\d-]{5,}$/;
  const message = regex.test(value)
    ? ""
    : "Phone must include country code (example: +1 213 555 0147)";
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
  const message = inputs.operatingCountry.value ? "" : "Select main operating country";
  setError("operatingCountry", message);
  setInputState(inputs.operatingCountry, message);
  return !message;
}

function validateProductType() {
  const message = inputs.productType.value ? "" : "Select the type of product you handle";
  setError("productType", message);
  setInputState(inputs.productType, message);
  updateVolumeWarning();
  return !message;
}

function validateMonthlyVolume() {
  const message = inputs.monthlyVolume.value ? "" : "Select estimated monthly volume";
  setError("monthlyVolume", message);
  setInputState(inputs.monthlyVolume, message);
  updateVolumeWarning();
  return !message;
}

function validateServicesInterest() {
  const message = getCheckedServicesCount() > 0 ? "" : "Select at least one service of interest";
  setError("servicesInterest", message);
  return !message;
}

function validateCurrent3pl() {
  const message = getCurrent3plValue() ? "" : "Indicate if you currently work with another logistics provider";
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
  const message = inputs.privacyPolicy.checked ? "" : "You must accept the privacy policy to continue";
  setError("privacyPolicy", message);
  return !message;
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

inputs.companyName.addEventListener("blur", validateCompanyName);
inputs.contactPerson.addEventListener("blur", validateContactPerson);
inputs.corporateEmail.addEventListener("blur", validateCorporateEmail);
inputs.phone.addEventListener("blur", validatePhone);
inputs.companyWebsite.addEventListener("blur", validateCompanyWebsite);
inputs.operatingCountry.addEventListener("change", validateOperatingCountry);
inputs.productType.addEventListener("change", validateProductType);
inputs.monthlyVolume.addEventListener("change", validateMonthlyVolume);
inputs.comments.addEventListener("input", validateComments);
inputs.privacyPolicy.addEventListener("change", validatePrivacyPolicy);

servicesCheckboxes.forEach((checkbox) => {
  checkbox.addEventListener("change", validateServicesInterest);
});

current3plRadios.forEach((radio) => {
  radio.addEventListener("change", validateCurrent3pl);
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

  if (!allValid) return;

  successMessage.textContent =
    "Thank you for your interest in TrackFlow! We have received your request. Our commercial team will review your information and contact you within the next 24-48 hours to schedule a call and learn about your logistics needs in detail. If you have any urgent inquiry, write to us directly at <comercial@trackflow.com>";

  form.reset();
  resetValidationState();
});

form.addEventListener("reset", () => {
  successMessage.textContent = "";
  // Wait a tick so native reset updates values before restoring UI state.
  setTimeout(() => {
    resetValidationState();
  }, 0);
});
