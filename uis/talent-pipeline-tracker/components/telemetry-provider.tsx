"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";
import {
  pageNameForPathname,
  trackFrontendError,
  trackPageNavigated,
  trackSlowPageLoad,
} from "@/lib/instrumentation";

/**
 * Piso técnico transversal del backoffice (Fase 3).
 *
 * Responsabilidades (todo bajo event_types aprobados en Fase 1):
 *  1. `page_navigated`: en cada cambio de ruta de una sección principal
 *     del backoffice se emite `page_navigated` con nombres lógicos de
 *     página. `navigation_method` por defecto es "nav_link" (la navegación
 *     principal del header es la entrada habitual); el schema permite
 *     nav_link/breadcrumb/direct_link/browser_back.
 *  2. Errores de frontend no capturados: `window.onerror` +
 *     `unhandledrejection` se mapean al canal aprobado
 *     `api_response_error_recorded` (endpoint sintético frontend:uncaught_error,
 *     decisión A documentada en memory-bank/progress.md).
 *  3. Métrica de rendimiento: al montar el layout raíz se mide el tiempo de
 *     carga (DOMContentLoaded del navigation timing) y si supera el umbral
 *     se emite `frontend:slow_page_load` sobre el mismo canal aprobado.
 *
 * Se monta una sola vez en app/layout.tsx (provider del layout raíz) y, al
 * importar `@/lib/instrumentation`, se arrastra `lib/telemetry.ts`, que se
 * auto-instancia (cola, beacon, visibilitychange).
 */
export function TelemetryProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const previousPageRef = useRef<string | null>(null);

  // --- page_navigated (navegación entre secciones del backoffice) -----------
  useEffect(() => {
    const toPage = pageNameForPathname(pathname);
    if (!toPage) return;

    const fromPage = previousPageRef.current;
    if (fromPage !== toPage) {
      trackPageNavigated(fromPage ?? "login", toPage, "nav_link");
    }
    previousPageRef.current = toPage;
  }, [pathname]);

  // --- Errores no capturados (window.onerror + unhandledrejection) ----------
  useEffect(() => {
    const onError = () => {
      trackFrontendError("uncaught_error");
    };
    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      if (event?.promise) trackFrontendError("unhandled_rejection");
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);

  // --- Métrica de rendimiento: tiempo de carga de la página (piso técnico) --
  useEffect(() => {
    if (typeof performance === "undefined" || !performance.getEntriesByType) return;
    const navEntries = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
    // Un único montaje en el layout raíz: el primer navigation entry es el
    // de la carga completa de la página (navegaciones cliente no crean uno nuevo).
    for (const entry of navEntries) {
      if (Number.isFinite(entry.domContentLoadedEventEnd)) {
        trackSlowPageLoad(entry.domContentLoadedEventEnd);
      }
    }
  }, []);

  return children;
}