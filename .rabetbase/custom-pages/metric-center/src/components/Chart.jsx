import React, { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const useReducedMotion = () => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return reduced;
};

const Chart = ({ option, height = 280, ariaLabel }) => {
  const elementRef = useRef(null);
  const chartRef = useRef(null);
  const reduced = useReducedMotion();
  const finalOption = useMemo(
    () => (option ? { ...option, animation: !reduced, animationDuration: 420 } : null),
    [option, reduced],
  );

  useEffect(() => {
    if (!elementRef.current) return undefined;
    const chart = echarts.init(elementRef.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    let observer;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(resize);
      observer.observe(elementRef.current);
    }
    return () => {
      window.removeEventListener("resize", resize);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (chartRef.current && finalOption) chartRef.current.setOption(finalOption, { notMerge: true });
  }, [finalOption]);

  return (
    <div
      ref={elementRef}
      className="metric-chart"
      role="img"
      aria-label={ariaLabel || ""}
      style={{ height }}
    />
  );
};

export default Chart;
