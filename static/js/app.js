(function () {
  "use strict";

  const currencyFormatter = new Intl.NumberFormat("en-IN", {
    currency: "INR",
    maximumFractionDigits: 0,
    style: "currency",
  });

  function text(value, fallback) {
    if (value === null || value === undefined || value === "") {
      return fallback || "";
    }
    return String(value);
  }

  function formatMoney(value) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) {
      return currencyFormatter.format(0);
    }
    return currencyFormatter.format(numberValue);
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

  function createElement(tagName, className, content) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (content !== undefined) {
      element.textContent = content;
    }
    return element;
  }

  function tableCell(content, className) {
    const cell = createElement("td", className || "");
    if (content instanceof Node) {
      cell.append(content);
    } else if (content !== undefined) {
      cell.textContent = content;
    }
    return cell;
  }

  function setStatus(status, title, message) {
    if (!status) {
      return;
    }

    status.replaceChildren(
      createElement("h3", "", title),
      createElement("p", "", message)
    );
  }

  function apiUrlWithCurrentFilters(apiUrl) {
    const url = new URL(apiUrl, window.location.origin);
    const currentParams = new URLSearchParams(window.location.search);

    currentParams.forEach(function (value, key) {
      if (value !== "") {
        url.searchParams.set(key, value);
      }
    });

    return url;
  }

  function detailUrl(template, listingId) {
    return template.replace(/0$/, String(listingId));
  }

  function addMeta(parent, label, value) {
    const item = createElement("div");
    item.append(
      createElement("dt", "", label),
      createElement("dd", "", text(value, "-"))
    );
    parent.append(item);
  }

  function listingCard(listing, detailTemplate) {
    const article = createElement("article", "listing-card");
    const imageLink = createElement("a", "listing-image-link");
    const url = detailUrl(detailTemplate, listing.id);
    imageLink.href = url;
    imageLink.setAttribute("aria-label", "View " + text(listing.title, "property"));

    const image = createElement("img");
    image.src = text(listing.image_url);
    image.alt = text(listing.title, "Rental property");
    imageLink.append(image);

    if (listing.is_featured) {
      imageLink.append(createElement("span", "tag", "Featured"));
    }
    if (Number.isFinite(Number(listing.similarity_score))) {
      imageLink.append(
        createElement(
          "span",
          "tag match-tag",
          Math.round(Number(listing.similarity_score) * 100) + "% match"
        )
      );
    }

    const body = createElement("div", "listing-body");
    const headingWrap = createElement("div");
    headingWrap.append(
      createElement(
        "p",
        "listing-location",
        text(listing.neighborhood) + ", " + text(listing.city)
      ),
      createElement("h3", "", text(listing.title, "Rental home"))
    );

    const price = createElement("p", "listing-price");
    price.append(
      document.createTextNode(formatMoney(listing.monthly_rent) + " "),
      createElement("span", "", "/ month")
    );

    const meta = createElement("dl", "meta-grid");
    addMeta(meta, "Beds", listing.bedrooms);
    addMeta(meta, "Baths", listing.bathrooms);
    addMeta(meta, "Area", text(listing.area_sqft, "-") + " sqft");

    const detailLink = createElement("a", "button-secondary", "View details");
    detailLink.href = url;

    body.append(headingWrap, price, meta, detailLink);
    article.append(imageLink, body);
    return article;
  }

  async function loadStorageStatus() {
    const status = document.querySelector("[data-storage-status]");
    if (!status) {
      return;
    }

    try {
      const response = await fetch(status.dataset.apiUrl, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("Storage status request failed");
      }
      const data = await response.json();
      status.textContent = text(data.storage_label, "API data");
    } catch (error) {
      status.textContent = "API unavailable";
    }
  }

  async function loadListings() {
    const grid = document.querySelector("[data-listings-grid]");
    if (!grid) {
      return;
    }

    const status = document.querySelector("[data-listings-status]");
    const count = document.querySelector("[data-listings-count]");
    const detailTemplate = grid.dataset.detailUrlTemplate;

    try {
      const response = await fetch(apiUrlWithCurrentFilters(grid.dataset.apiUrl), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("Listings request failed");
      }

      const data = await response.json();
      const listings = Array.isArray(data.listings) ? data.listings : [];
      if (count) {
        count.textContent = String(data.count ?? listings.length);
      }

      if (listings.length === 0) {
        grid.hidden = true;
        setStatus(
          status,
          "No homes match these filters.",
          "Try another city, wider rent range, or fewer bedroom requirements."
        );
        status.hidden = false;
        return;
      }

      grid.replaceChildren(
        ...listings.map(function (listing) {
          return listingCard(listing, detailTemplate);
        })
      );
      status.hidden = true;
      grid.hidden = false;
    } catch (error) {
      grid.hidden = true;
      if (count) {
        count.textContent = "0";
      }
      setStatus(
        status,
        "Listings could not be loaded.",
        "Check that the Flask API is running and try again."
      );
      status.hidden = false;
    }
  }

  function setPropertyText(selector, value, fallback) {
    const element = document.querySelector(selector);
    if (element) {
      element.textContent = text(value, fallback);
    }
  }

  function renderAmenities(listing) {
    const amenities = document.querySelector("[data-property-amenities]");
    if (!amenities) {
      return;
    }

    const items = text(listing.amenities)
      .split(",")
      .map(function (item) {
        return item.trim();
      })
      .filter(Boolean);

    amenities.replaceChildren(
      ...items.map(function (item) {
        return createElement("li", "", item);
      })
    );
  }

  function renderProperty(listing) {
    document.title = text(listing.title, "Property Details") + " - RoomRent";

    const image = document.querySelector("[data-property-image]");
    if (image) {
      image.src = text(listing.image_url);
      image.alt = text(listing.title, "Rental property");
    }

    setPropertyText(
      "[data-property-type-city]",
      text(listing.property_type, "Property") + " in " + text(listing.city, "")
    );
    setPropertyText("[data-property-title]", listing.title, "Rental home");
    setPropertyText("[data-property-address]", listing.address);
    setPropertyText("[data-property-price]", formatMoney(listing.monthly_rent) + " / month");
    setPropertyText("[data-property-rent]", formatMoney(listing.monthly_rent));
    setPropertyText("[data-property-deposit]", formatMoney(listing.deposit));
    setPropertyText("[data-property-bedrooms]", listing.bedrooms);
    setPropertyText("[data-property-bathrooms]", listing.bathrooms);
    setPropertyText("[data-property-area]", text(listing.area_sqft, "-") + " sqft");
    setPropertyText("[data-property-furnished]", listing.furnished_status);
    setPropertyText("[data-property-description]", listing.description);
    setPropertyText(
      "[data-property-owner]",
      text(listing.owner_name, "Owner") + " - " + text(listing.owner_phone)
    );

    const featured = document.querySelector("[data-property-featured]");
    if (featured) {
      featured.hidden = !listing.is_featured;
    }

    const listingId = document.querySelector("[data-property-listing-id]");
    if (listingId) {
      listingId.value = text(listing.id);
    }

    renderAmenities(listing);

    const content = document.querySelector("[data-property-content]");
    if (content) {
      content.hidden = false;
    }
  }

  async function loadProperty() {
    const page = document.querySelector("[data-property-page]");
    if (!page) {
      return;
    }

    const content = document.querySelector("[data-property-content]");
    const error = document.querySelector("[data-property-error]");

    try {
      const response = await fetch(page.dataset.apiUrl, {
        headers: { Accept: "application/json" },
      });
      if (response.status === 404) {
        throw new RangeError("Property not found");
      }
      if (!response.ok) {
        throw new Error("Property request failed");
      }

      const data = await response.json();
      renderProperty(data.listing || {});
    } catch (loadError) {
      page.hidden = true;
      if (content) {
        content.hidden = true;
      }
      if (error) {
        error.hidden = false;
      }
      document.title = "Property Not Found - RoomRent";
    }
  }

  async function loadRecommendations() {
    const section = document.querySelector("[data-recommendations-section]");
    if (!section) {
      return;
    }

    const grid = section.querySelector("[data-recommendations-grid]");
    const status = section.querySelector("[data-recommendations-status]");
    const detailTemplate = section.dataset.detailUrlTemplate;

    section.hidden = false;
    if (status) {
      status.hidden = false;
    }

    try {
      const response = await fetch(section.dataset.apiUrl, {
        headers: { Accept: "application/json" },
      });
      if (response.status === 404) {
        throw new RangeError("Property not found");
      }
      if (!response.ok) {
        throw new Error("Recommendations request failed");
      }

      const data = await response.json();
      const recommendations = Array.isArray(data.recommendations)
        ? data.recommendations
        : [];

      if (recommendations.length === 0) {
        if (grid) {
          grid.hidden = true;
        }
        setStatus(
          status,
          "No similar rooms yet.",
          "Add more available listings to improve the recommendation model."
        );
        if (status) {
          status.hidden = false;
        }
        return;
      }

      if (grid) {
        grid.replaceChildren(
          ...recommendations.map(function (listing) {
            return listingCard(listing, detailTemplate);
          })
        );
        grid.hidden = false;
      }
      if (status) {
        status.hidden = true;
      }
    } catch (error) {
      section.hidden = true;
    }
  }

  function emptyTableRow(columnCount, message) {
    const row = createElement("tr");
    const cell = tableCell(message, "ops-empty-row");
    cell.colSpan = columnCount;
    row.append(cell);
    return row;
  }

  function userIdentity(name, secondary) {
    const wrap = createElement("div", "ops-user-cell");
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

  function renderOperationStats(data) {
    const stats = data.stats || {};
    document.querySelectorAll("[data-ops-stat]").forEach(function (element) {
      const key = element.dataset.opsStat;
      element.textContent = String(stats[key] ?? 0);
    });

    const updated = document.querySelector("[data-ops-updated]");
    if (updated) {
      updated.textContent = "Updated " + formatDateTime(data.generated_at);
    }

    const storage = document.querySelector("[data-ops-storage]");
    if (storage) {
      storage.textContent = text(data.storage_label, "API data");
    }

    const active = document.querySelector("[data-ops-active]");
    if (active) {
      active.textContent = String(stats.active_last_7_days ?? 0) + " active";
    }
  }

  function renderOperationUsers(users) {
    const body = document.querySelector("[data-ops-users]");
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
          tableCell(userIdentity(user.name, user.id), "ops-primary-cell"),
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

  function renderOperationFootprints(footprints) {
    const body = document.querySelector("[data-ops-footprints]");
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
        const device = createElement("div", "ops-device-cell");
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
            "ops-primary-cell"
          ),
          tableCell(labelize(footprint.event_type)),
          tableCell(text(footprint.method) + " " + text(footprint.path, "/")),
          tableCell(device)
        );
        return row;
      })
    );
  }

  async function loadOperationsDashboard() {
    const dashboard = document.querySelector("[data-operations-dashboard]");
    if (!dashboard) {
      return;
    }

    const errorPanel = document.querySelector("[data-operations-error]");

    try {
      const response = await fetch(dashboard.dataset.apiUrl, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("Operations dashboard request failed");
      }

      const data = await response.json();
      renderOperationStats(data);
      renderOperationUsers(Array.isArray(data.recent_users) ? data.recent_users : []);
      renderOperationFootprints(
        Array.isArray(data.recent_footprints) ? data.recent_footprints : []
      );
    } catch (loadError) {
      dashboard.hidden = true;
      if (errorPanel) {
        errorPanel.hidden = false;
      }
    }
  }

  function initProfileToggle() {
    const menu = document.querySelector("[data-profile-menu]");
    const toggle = document.querySelector("[data-profile-toggle]");
    const popover = document.querySelector("[data-profile-popover]");

    if (!menu || !toggle || !popover) {
      return;
    }

    function setOpen(isOpen) {
      popover.hidden = !isOpen;
      toggle.setAttribute("aria-expanded", String(isOpen));
    }

    toggle.addEventListener("click", function () {
      setOpen(popover.hidden);
    });

    document.addEventListener("click", function (event) {
      if (!popover.hidden && !menu.contains(event.target)) {
        setOpen(false);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    });
  }

  function initFlashDismiss() {
    const flashes = document.querySelectorAll(".flash-success, .flash-info");
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

  function initAvatarFallbacks() {
    document.querySelectorAll("[data-avatar-image]").forEach(function (image) {
      image.addEventListener("error", function () {
        const fallback = image.parentElement.querySelector(
          "[data-avatar-fallback]"
        );
        image.hidden = true;
        if (fallback) {
          fallback.hidden = false;
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initProfileToggle();
    initFlashDismiss();
    initAvatarFallbacks();
    loadStorageStatus();
    loadListings();
    loadProperty();
    loadRecommendations();
    loadOperationsDashboard();
  });
})();
