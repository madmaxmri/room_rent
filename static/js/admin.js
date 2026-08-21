(function () {
  "use strict";

  const currencyFormatter = new Intl.NumberFormat("en-IN", {
    currency: "INR",
    maximumFractionDigits: 0,
    style: "currency",
  });

  const numberFormatter = new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 0,
  });

  function text(value, fallback) {
    if (value === null || value === undefined || value === "") {
      return fallback || "";
    }
    return String(value);
  }

  function formatDateTime(value) {
    if (!value) {
      return "-";
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "-";
    }

    return date.toLocaleString("en-IN", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  }

  function labelize(value) {
    return text(value, "activity")
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (letter) {
        return letter.toUpperCase();
      });
  }

  function formatMoney(value) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) {
      return currencyFormatter.format(0);
    }
    return currencyFormatter.format(numberValue);
  }

  function formatNumber(value) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) {
      return "0";
    }
    return numberFormatter.format(numberValue);
  }

  function createElement(tagName, className, content) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (content instanceof Node) {
      element.append(content);
    } else if (content !== undefined) {
      element.textContent = content;
    }
    return element;
  }

  function tableCell(content, className) {
    return createElement("td", className || "", content);
  }

  function emptyTableRow(columnCount, message) {
    const row = createElement("tr");
    const cell = tableCell(message, "admin-empty-row");
    cell.colSpan = columnCount;
    row.append(cell);
    return row;
  }

  function userIdentity(name, secondary) {
    const wrap = createElement("div", "admin-user-cell");
    wrap.append(createElement("strong", "", text(name, "User")));
    if (secondary) {
      wrap.append(createElement("span", "", secondary));
    }
    return wrap;
  }

  function compactUserAgent(userAgent) {
    const value = text(userAgent, "Unknown device");
    if (value.length <= 72) {
      return value;
    }
    return value.slice(0, 69) + "...";
  }

  function renderStats(data) {
    const stats = data.stats || {};
    document.querySelectorAll("[data-admin-stat]").forEach(function (element) {
      const key = element.dataset.adminStat;
      element.textContent = String(stats[key] ?? 0);
    });

    const updated = document.querySelector("[data-admin-updated]");
    if (updated) {
      updated.textContent = "Updated " + formatDateTime(data.generated_at);
    }

    const storage = document.querySelector("[data-admin-storage]");
    if (storage) {
      storage.textContent = text(data.storage_label, "Database");
    }

    const active = document.querySelector("[data-admin-active]");
    if (active) {
      active.textContent = String(stats.active_last_7_days ?? 0) + " active";
    }
  }

  function renderUsers(users) {
    const body = document.querySelector("[data-admin-users]");
    if (!body) {
      return;
    }

    if (!users.length) {
      body.replaceChildren(emptyTableRow(7, "No users found."));
      return;
    }

    body.replaceChildren(
      ...users.map(function (user) {
        const row = createElement("tr");
        const contact = text(user.email) || text(user.phone, "-");
        row.append(
          tableCell(userIdentity(user.name, user.id), "admin-primary-cell"),
          tableCell(contact),
          tableCell(text(user.auth_label, user.auth_method)),
          tableCell(formatDateTime(user.created_at)),
          tableCell(formatDateTime(user.last_login_at)),
          tableCell(String(user.footprint_count ?? 0)),
          tableCell(
            text(user.last_event_type)
              ? labelize(user.last_event_type) + " - " + text(user.last_path, "/")
              : "-"
          )
        );
        return row;
      })
    );
  }

  function renderFootprints(footprints) {
    const body = document.querySelector("[data-admin-footprints]");
    if (!body) {
      return;
    }

    if (!footprints.length) {
      body.replaceChildren(emptyTableRow(5, "No footprints recorded yet."));
      return;
    }

    body.replaceChildren(
      ...footprints.map(function (footprint) {
        const row = createElement("tr");
        const device = createElement("div", "admin-device-cell");
        device.append(
          createElement("span", "", compactUserAgent(footprint.user_agent)),
          createElement("strong", "", text(footprint.ip_address, "-"))
        );

        row.append(
          tableCell(formatDateTime(footprint.created_at)),
          tableCell(
            userIdentity(
              footprint.user_name,
              text(footprint.user_contact, footprint.auth_label)
            ),
            "admin-primary-cell"
          ),
          tableCell(labelize(footprint.event_type)),
          tableCell(text(footprint.method) + " " + text(footprint.path, "/")),
          tableCell(device)
        );
        return row;
      })
    );
  }

  function renderAnalyticsStats(data) {
    const summary = data.summary || {};
    document.querySelectorAll("[data-admin-analytics-stat]").forEach(function (element) {
      const key = element.dataset.adminAnalyticsStat;
      if (key === "average_rent") {
        element.textContent = formatMoney(summary[key]);
      } else if (key === "footprint_count") {
        element.textContent = formatNumber(data.footprint_count);
      } else {
        element.textContent = formatNumber(summary[key]);
      }
    });

    const updated = document.querySelector("[data-admin-analytics-updated]");
    if (updated) {
      updated.textContent =
        text(data.storage_label, "Database") + " - " + formatDateTime(data.generated_at);
    }
  }

  function renderAnalyticsCharts(charts) {
    const grid = document.querySelector("[data-admin-charts]");
    if (!grid) {
      return;
    }

    if (!charts.length) {
      grid.replaceChildren(
        createElement("article", "admin-chart-card", "No analytics charts available.")
      );
      return;
    }

    grid.replaceChildren(
      ...charts.map(function (chart) {
        const card = createElement("article", "admin-chart-card");
        const heading = createElement("h3", "", text(chart.title, "Analytics"));
        const image = createElement("img");
        image.src = text(chart.image);
        image.alt = text(chart.alt, text(chart.title, "Analytics chart"));
        card.append(heading, image);
        return card;
      })
    );
  }

  function renderTopCities(cities) {
    const container = document.querySelector("[data-admin-top-cities]");
    if (!container) {
      return;
    }

    if (!cities.length) {
      container.replaceChildren(createElement("p", "", "No city data available."));
      return;
    }

    const list = createElement("div", "admin-city-list");
    cities.forEach(function (city) {
      const row = createElement("article", "admin-city-row");
      row.append(
        createElement("strong", "", text(city.city, "Unknown")),
        createElement(
          "span",
          "",
          formatNumber(city.listings) + " listings - " + formatMoney(city.average_rent)
        )
      );
      list.append(row);
    });
    container.replaceChildren(list);
  }

  async function loadDashboard() {
    const dashboard = document.querySelector("[data-admin-dashboard]");
    if (!dashboard) {
      return;
    }

    try {
      const response = await fetch(dashboard.dataset.apiUrl, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("Admin dashboard request failed");
      }

      const data = await response.json();
      renderStats(data);
      renderUsers(Array.isArray(data.recent_users) ? data.recent_users : []);
      renderFootprints(
        Array.isArray(data.recent_footprints) ? data.recent_footprints : []
      );
    } catch (error) {
      document.querySelectorAll("[data-admin-users], [data-admin-footprints]").forEach(
        function (body) {
          const columns = body.matches("[data-admin-users]") ? 7 : 5;
          body.replaceChildren(
            emptyTableRow(columns, "Admin dashboard data could not be loaded.")
          );
        }
      );
    }
  }

  async function loadAnalytics() {
    const dashboard = document.querySelector("[data-admin-dashboard]");
    if (!dashboard || !dashboard.dataset.analyticsUrl) {
      return;
    }

    try {
      const response = await fetch(dashboard.dataset.analyticsUrl, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("Admin analytics request failed");
      }

      const data = await response.json();
      renderAnalyticsStats(data);
      renderAnalyticsCharts(Array.isArray(data.charts) ? data.charts : []);
      renderTopCities(Array.isArray(data.top_cities) ? data.top_cities : []);
    } catch (error) {
      const grid = document.querySelector("[data-admin-charts]");
      if (grid) {
        grid.replaceChildren(
          createElement(
            "article",
            "admin-chart-card",
            "Analytics could not be loaded."
          )
        );
      }
    }
  }

  function initFlashDismiss() {
    const flashes = document.querySelectorAll(
      ".admin-flash-success, .admin-flash-info, .flash-success, .flash-info"
    );
    if (flashes.length === 0) {
      return;
    }

    window.setTimeout(function () {
      flashes.forEach(function (flash) {
        flash.classList.add("is-dismissing");
        window.setTimeout(function () {
          flash.remove();
        }, 220);
      });
    }, 2000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initFlashDismiss();
    loadDashboard();
    loadAnalytics();
  });
})();
