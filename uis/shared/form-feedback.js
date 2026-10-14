class TrackFlowFeedback extends HTMLElement {
  connectedCallback() {
    if (!this.hasAttribute("role")) this.setAttribute("role", "status");
    if (!this.hasAttribute("aria-live")) this.setAttribute("aria-live", "polite");
  }

  setMessage(message) {
    this.textContent = message || "";
  }
}

customElements.define("trackflow-feedback", TrackFlowFeedback);