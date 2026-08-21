-- Optional explanatory text displayed next to an individual ski-pass price.
ALTER TABLE ski_pass_prices
  ADD COLUMN IF NOT EXISTS note TEXT;
