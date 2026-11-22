using System;
using System.Collections.Generic;

public class CPHInline
{
    public bool Execute()
    {
        try
        {
            string user = args["user"].ToString();
            string userId = args["userId"].ToString();

            int streamId = CPH.GetGlobalVar<int>("first_stream_id", true);

            // get or create list of current redeemers
            List<string> redeemers = CPH.GetGlobalVar<List<string>>("current_first_redeemers", true) ?? new List<string>();

            // skip if user already redeemed this stream
            if (redeemers.Contains(userId))
            {
                CPH.SendMessage($"{user}, you already claimed FIRST this stream!");
                return true;
            }

            // already a VIP (not one of today's FIRST VIPs): leave their VIP alone, or the
            // end-of-stream cleanup would take away a VIP they earned some other way
            List<string> todaysVips = CPH.GetGlobalVar<List<string>>("temp_first_vips", true) ?? new List<string>();
            if (args.ContainsKey("isVip") && args["isVip"].ToString() == "True" && !todaysVips.Contains(user))
            {
                CPH.SendMessage($"{user} was FIRST! You're already a VIP, so nothing changes for you. GG!");
                return true;
            }

            // mark user as redeemed
            redeemers.Add(userId);
            CPH.SetGlobalVar("current_first_redeemers", redeemers, true);

            // check if permanent
            bool permanent = CPH.GetTwitchUserVarById<bool>(userId, "first_permanent", true);
            if (permanent)
            {
                CPH.TwitchAddVip(user);   // in case it was lost (the old script removed it at the end of the 5th stream)
                CPH.SendMessage($"{user} already has permanent VIP!");
                return true;
            }

            // streak tracking
            int lastStream = CPH.GetTwitchUserVarById<int>(userId, "first_last_stream", true);
            int streak = CPH.GetTwitchUserVarById<int>(userId, "first_streak", true);

            if (lastStream == streamId - 1)
                streak++;
            else
                streak = 1;

            CPH.SetTwitchUserVarById(userId, "first_streak", streak, true);
            CPH.SetTwitchUserVarById(userId, "first_last_stream", streamId, true);

            // give VIP
            CPH.TwitchAddVip(user);

            // track temporary VIPs for removal at stream end
            List<string> tempVips = CPH.GetGlobalVar<List<string>>("temp_first_vips", true) ?? new List<string>();
            tempVips.Add(user);
            CPH.SetGlobalVar("temp_first_vips", tempVips, true);

            // messages
            if (streak >= 5)
            {
                CPH.SetTwitchUserVarById(userId, "first_permanent", true, true);
                tempVips.Remove(user);   // permanent: keep the VIP at the end of this stream
                CPH.SetGlobalVar("temp_first_vips", tempVips, true);
                CPH.SendMessage($"{user} earned PERMANENT VIP by redeeming FIRST 5 streams in a row!");
            }
            else
            {
                CPH.SendMessage($"{user} was FIRST! Streak: {streak}/5");
            }

            return true;
        }
        catch (Exception ex)
        {
            CPH.LogError($"FIRST SCRIPT ERROR: {ex.Message}");
            return false;
        }
    }
}