pub trait Strategy {
    fn name(&self) -> &str;
    fn on_tick(&mut self, price: f64);
}