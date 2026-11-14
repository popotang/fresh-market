import React, { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const Chart = ({ option, height = 300 }) => {
  const elementRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    const chart = echarts.init(elementRef.current);
    chartRef.current = chart;
    const resize = () => chart.resize();
    let observer;
    window.addEventListener("resize", resize);
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(resize);
      observer.observe(elementRef.current);
    }
    return () => {
      window.removeEventListener("resize", resize);
      if (observer) observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (chartRef.current && option) chartRef.current.setOption(option, { notMerge: true });
  }, [option]);

  return <div className="report-chart" style={{ height }} ref={elementRef} />;
};

export default Chart;
