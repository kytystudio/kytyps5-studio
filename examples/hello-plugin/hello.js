"use strict";
(function (K) {
  const { h } = K, hello = K.plugin("hello");
  let last = null;
  K.registerTab({
    id: "hello",
    label: "Hello",
    render() {
      return h("div.view.hello-view", {},
        h("h2", { text: last ? last.message : "Hello example" }),
        h("p", { text: last ? `Visit ${last.visits} at ${last.time}` : "Press the button to call this add-on's backend." }),
        h("button.pill", { "data-f": true, "data-default": true, on: { click: async () => { last = await hello.call("greet", { name: K.state.users?.[0]?.name }); K.renderView(); } } }, "Say hello"));
    },
  });
  K.registerSection({ label: "Hello example", rows: () => [K.row({ label: "About", desc: "This section comes from the example add-on." })] });
})(window.KytyStudio);
