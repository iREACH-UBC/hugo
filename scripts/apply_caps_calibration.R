# ── apply_caps_calibration.R ─────────────────────────────────────────────
apply_caps_calibration <- function(sensor_id,
                                   data_file,
                                   bucket   = "outdoor-calibrations",
                                   tz_raw   = "Etc/GMT-8",
                                   avg_time = "15 min",
                                   out_dir  = NULL) {
  
  ## 1 ── Download & load model object from R2 ─────────────────────────────
  library(aws.s3); library(glue)
  
  base_url <- Sys.getenv("R2_ENDPOINT")
  if (base_url == "")
    stop("R2_ENDPOINT env-var not set (e.g. xxxx.r2.cloudflarestorage.com)")
  base_url <- sub("^https?://", "", base_url)
  
  model_key <- glue("{sensor_id}/Calibration_Models.obj")
  message(glue("→ Downloading {sensor_id} model from R2 …"))
  raw_obj <- aws.s3::get_object(
    object   = model_key,
    bucket   = bucket,
    base_url = base_url,
    region   = ""
  )
  
  # ── robust loader that *still* uses seek() ------------------------------
  hdr      <- rawToChar(raw_obj[1:3])                 # gzip header = 1F 8B 08
  is_gzip  <- identical(hdr, "\x1f\x8b\b")
  raw_con  <- rawConnection(raw_obj, open = "rb")     # seek-able connection
  load_env <- new.env(parent = emptyenv())
  
  # — try to load() first ---------------------------------------------------
  try_ok <- tryCatch({
    if (is_gzip) {
      load(gzcon(raw_con), envir = load_env, verbose = FALSE)
    } else {
      load(raw_con,        envir = load_env, verbose = FALSE)
    }
    TRUE
  }, error = function(e) FALSE)
  
  if (!try_ok) {
    # rewind and fall back to readRDS(), but *use seek() as requested*
    seek(raw_con, where = 0L, origin = "start")
    
    if (is_gzip) {
      calibration_models <- readRDS(gzcon(raw_con))
    } else {
      calibration_models <- readRDS(raw_con)
    }
  } else {
    calibration_models <- load_env$calibration_models
  }
  
  close(raw_con)   # now safe to close
  
  ## 2 ── Libraries for downstream work -------------------------------------
  suppressPackageStartupMessages({
    library(dplyr);        library(readr);  library(lubridate); library(tibble)
    library(openair);      library(fs);     library(gtools);    library(tidyr)
    library(purrr);        library(randomForest)
  })
  
  message("→ Loading CAPS helpers and models …")
  source("caps_core.R", local = TRUE)      # brings in CAPS_* helpers
  
  ## 3 ── Read & tidy raw logger file ---------------------------------------
  raw <- read_csv(data_file, col_names = FALSE, show_col_types = FALSE)
  names(raw) <- paste0("V", seq_along(raw))
  
  raw <- raw |>
    select(V1, V3, V4, V5, V6, V7, V8, V9, V11) |>
    rlang::set_names(
      c("date","CO_RAMP","NO_RAMP","NO2_RAMP","O3_RAMP",
        "CO2_RAMP","T_RAMP","RH_RAMP","PM_RAMP")
    ) |>
    mutate(
      date  = parse_date_time(date, orders = "%m/%d/%y %H:%M:%S"),
      across(-date, parse_number)
    ) |>
    filter(!is.na(date))
  
  raw$date <- force_tz(raw$date, tz_raw)
  
  ## daylight-saving shim ----------------------------------------------------
  raw <- bind_rows(
    filter(raw, date < "2025-03-09 02:00:00"),
    filter(raw, date >= "2025-03-09 02:00:00") |>
      mutate(date = date - 3600)
  )
  
  ## 4 ── 15-min averages ----------------------------------------------------
  ramp_15 <- openair::timeAverage(raw, avg.time = avg_time)
  
  ## predictors --------------------------------------------------------------
  gas_mat <- ramp_15 |>
    select(CO_RAMP, NO_RAMP, NO2_RAMP, O3_RAMP,
           CO2_RAMP, T_RAMP, RH_RAMP) |>
    as.matrix()
  
  pm_mat  <- ramp_15 |>
    select(T_RAMP, RH_RAMP) |>
    mutate(DP_RAMP = 243.12 *
             (log(RH_RAMP/100) + 17.62*T_RAMP / (243.12 + T_RAMP)) /
             (17.62 - (log(RH_RAMP/100) + 17.62*T_RAMP / (243.12 + T_RAMP)))) |>
    as.matrix()
  
  ## 5 ── Apply CAPS models --------------------------------------------------
  message("→ Applying calibration …")
  pred <- tibble(
    date  = ramp_15$date,
    NO2   = as.numeric(CAPS_Hybrid_Apply(calibration_models$gas$Hybrid$NO2,  gas_mat)),
    NO    = as.numeric(CAPS_Hybrid_Apply(calibration_models$gas$Hybrid$NO,   gas_mat)),
    CO2   = as.numeric(CAPS_Hybrid_Apply(calibration_models$gas$Hybrid$CO2,  gas_mat)),
    O3    = as.numeric(CAPS_Hybrid_Apply(calibration_models$gas$Hybrid$O3,   gas_mat)),
    CO    = as.numeric(CAPS_Hybrid_Apply(calibration_models$gas$Hybrid$CO,   gas_mat)),
    PM2_5 = as.numeric(CAPS_PR_Apply   (calibration_models$pm$Regression$PM2_5, pm_mat))
  )
  
  ## 6 ── Optional write-out --------------------------------------------------
  if (!is.null(out_dir)) {
    fs::dir_create(out_dir)
    out_file <- file.path(
      out_dir,
      sprintf("%s_%s_calibrated.csv",
              sensor_id,
              tools::file_path_sans_ext(basename(data_file)))
    )
    readr::write_csv(pred, out_file)
    message("✔ Calibrated file saved ➜ ", out_file)
  }
  
  invisible(pred)
}
